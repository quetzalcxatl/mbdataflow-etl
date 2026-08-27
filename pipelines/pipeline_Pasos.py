# pipelines/pipeline_Pasos.py
# -*- coding: utf-8 -*-
"""
Orquestador del pipeline de Reporte de Pasos por parada.

Flujo EL puro — no hay etapa de transform ni carga a BigQuery. El destino
final del dato es Google Drive.

Ejecución:
    python -m pipelines.pipeline_Pasos

Grafo de dependencias:

    scrape (Sonda -> 10 CSV crudos, uno por línea operativa,
            ventana lunes 03:20 → lunes 03:20)
        │
        └── drive_load(list[Path]) -> reporte_de_pasos/<año ISO>/<semana ISO>/
                                      [MEDULAR, best-effort + raise final]

Semana operativa (§ contrato compartido con Viaje):
Ver utils.dates.last_completed_operational_week_cdmx — ancla en lunes 03:20
CDMX para respetar el corte real de servicio de Metrobús. El rango es
SEMIABIERTO [lunes 03:20, próximo lunes 03:20). El scraper aplica
dt_f = end - 1 minuto porque el SPA de Pasos tiene granularidad de minuto.


Fallos (§5.6):
  - Todo lanza excepción y sale con exit code != 0. Nada silencioso.
  - El loader de Drive es best-effort POR ARCHIVO pero levanta RuntimeError al
    final si falló al menos uno — los 10 se intentan, el Job igual queda FAILED.
  - Validación de env vars al INICIO: si falta algo, falla en el segundo 1 en
    vez de tras ~40 minutos de scraping de 10 líneas.

Modo local: el scraper corre headless por default. Para ver el navegador,
setear SCRAPER_HEADLESS=false antes de ejecutar.
"""

from __future__ import annotations

import sys

from config.settings import (
    SONDA_QUERY_USER,
    SONDA_QUERY_PASSWORD,
    DRIVE_PASOS_FOLDER_ID,
)
from extract.scrapers.Pasos import Pasos_Scraper, LINEAS_OPERATIVAS
from load.loaders.Pasos_drive_loader import Pasos_load_to_drive
from utils.dates import last_completed_operational_week_cdmx
from utils.logger import ok, info, err


# --------------------------------------------------------------------------
# Validación temprana de configuración
# --------------------------------------------------------------------------
def _validate_env() -> str:
    """
    Verifica que la configuración esté completa ANTES de abrir Chrome.

    Un env var faltante detectado aquí cuesta un segundo; detectado después del
    scrape cuesta los ~40 minutos que tarda iterar las 10 líneas contra Sonda.

    Retorna: el folder ID raíz de 'reporte_de_pasos'.
    """
    faltantes = []
    for var, val in [
        ("SONDA_QUERY_USER",      SONDA_QUERY_USER),
        ("SONDA_QUERY_PASSWORD",  SONDA_QUERY_PASSWORD),
        ("DRIVE_PASOS_FOLDER_ID", DRIVE_PASOS_FOLDER_ID),
    ]:
        if not val:
            faltantes.append(var)

    if faltantes:
        raise RuntimeError(
            "pipeline_Pasos: configuración incompleta. Faltan env vars: "
            + ", ".join(faltantes)
        )

    return DRIVE_PASOS_FOLDER_ID


# --------------------------------------------------------------------------
# Destino en Drive
# --------------------------------------------------------------------------
def _iso_destino(start) -> tuple[int, int]:
    """
    Deriva (año ISO, semana ISO) del lunes-03:20 que abre la semana operativa.

    POR QUÉ ISO Y NO AÑO CALENDARIO
    -------------------------------
    utils.dates.last_operational_week_number() retorna start.isocalendar()[1],
    es decir semana ISO. Emparejar una semana ISO con un año CALENDARIO produce
    colisiones reales, no teóricas:

        lunes 2024-01-01 -> isocalendar (2024, W01), start.year 2024 -> 2024/01
        lunes 2024-12-30 -> isocalendar (2025, W01), start.year 2024 -> 2024/01

    Dos semanas operativas separadas por 52 semanas cayendo en la misma carpeta.
    Los nombres de archivo no salvan nada: el scraper también usa la semana ISO
    para el sufijo 'sem{N}'. Cualquier año que empiece en lunes lo reproduce.

    El par (año ISO, semana ISO) es único por construcción. Ese es el que se usa.

    POR QUÉ NO SE LLAMA A last_operational_week_number()
    ---------------------------------------------------
    Ese helper invoca datetime.now() por su cuenta, independiente del now() que
    ya consumió last_completed_operational_week_cdmx(). Arrancando un lunes a
    las 03:19:59 las dos llamadas pueden caer en semanas distintas. Como el
    helper es por definición start.isocalendar()[1], derivar ambos valores de
    la MISMA llamada a isocalendar() sobre el MISMO start elimina la carrera
    sin cambiar el resultado.

    CARRERA RESIDUAL (conocida, no mitigable desde aquí)
    ---------------------------------------------------
    Pasos_Scraper.scrape() llama a ambos helpers internamente para armar el
    nombre del archivo. Si esa llamada cruza la frontera de las 03:20, el
    'sem{N}' del nombre podría diferir del N de la carpeta destino. Cerrarlo
    exige que scrape() reciba la ventana por parámetro en vez de calcularla —
    refactor fuera del scope de este PR.
    """
    iso_year, iso_week, _ = start.isocalendar()
    return iso_year, iso_week


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    info("=" * 60)
    info("  pipeline_Pasos — arranque")
    info("=" * 60)

    # 0. Validar configuración ANTES de abrir Chrome
    drive_root = _validate_env()

    # Semana operativa: lunes 03:20 → lunes 03:20 CDMX.
    # UNA sola lectura del reloj; año y semana se derivan de este `start`.
    start, end = last_completed_operational_week_cdmx()
    iso_year, iso_week = _iso_destino(start)

    info(f"Semana operativa: {start.strftime('%Y-%m-%d %H:%M %Z')} .. "
         f"{end.strftime('%Y-%m-%d %H:%M %Z')}")
    info(f"Destino en Drive: reporte_de_pasos/{iso_year:04d}/{iso_week:02d}")
    info(f"Líneas operativas a procesar: {len(LINEAS_OPERATIVAS)}")

    # 1. Extract: scrape de las 10 líneas
    info("─ Etapa 1/2: Extract ─────────────────────────────────")
    scraper = Pasos_Scraper()
    raw_csvs = scraper.scrape()

    # El scraper no captura excepciones por línea: si una falla, la corrida
    # entera aborta. Un conteo corto aquí significaría que el contrato del
    # scraper cambió sin avisar — vale la pena que sea ruidoso.
    if len(raw_csvs) != len(LINEAS_OPERATIVAS):
        raise RuntimeError(
            f"pipeline_Pasos: el scraper devolvió {len(raw_csvs)} archivos, "
            f"se esperaban {len(LINEAS_OPERATIVAS)} (uno por línea operativa)."
        )
    ok(f"Extract completo: {len(raw_csvs)} archivos")

    # 2. Load: los 10 CSV a reporte_de_pasos/<año ISO>/<semana ISO>/
    info("─ Etapa 2/2: Load -> Google Drive ────────────────────")
    resultado = Pasos_load_to_drive(
        file_paths=raw_csvs,
        root_folder_id=drive_root,
        year=iso_year,
        week_number=iso_week,
    ).run()
    ok(f"Load completo: {len(resultado)} archivos en Drive")

    info("=" * 60)
    ok(f"  pipeline_Pasos: semana operativa "
       f"{start.strftime('%Y-%m-%d %H:%M')}..{end.strftime('%Y-%m-%d %H:%M')} "
       f"completada")
    info("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # §5.6: cualquier fallo NO capturado explícitamente propaga con exit != 0.
        # Cloud Run lo reporta FAILED y la alerta de Cloud Monitoring dispara.
        err(f"pipeline_Pasos FALLÓ: {type(e).__name__}: {e}")
        raise