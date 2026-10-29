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

Modo reposición (backfill):
    python -m pipelines.pipeline_Pasos --desde 2026-09-28 --hasta 2026-10-12

  - --desde / --hasta son LUNES (YYYY-MM-DD); la hora 03:20 CDMX es implícita.
    --hasta es exclusivo (lunes de cierre). El intervalo se parte en semanas
    operativas y cada una se scrapea y carga por separado.
  - El loader omite archivos que ya existen por nombre: los CSV a reponer deben
    BORRARSE A MANO en Drive antes. Si alguno sigue ahí, la corrida aborta
    antes de abrir Chrome.
  - En Cloud Run, tope de MAX_SEMANAS_CLOUD_RUN por ejecución (task-timeout 2h):
    gcloud run jobs execute pipeline-pasos --region=us-central1 \\
      --args="-m,pipelines.pipeline_Pasos,--desde,2026-09-28,--hasta,2026-10-12"
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

from config.settings import (
    SONDA_QUERY_USER,
    SONDA_QUERY_PASSWORD,
    DRIVE_PASOS_FOLDER_ID,
)
from extract.scrapers.Pasos import Pasos_Scraper, LINEAS_OPERATIVAS, LINEA_SLUG, nombre_csv
from load.loaders.Pasos_drive_loader import (
    Pasos_load_to_drive,
    _year_folder_name,
    _week_folder_name,
)
from utils.dates import CDMX_TZ, last_completed_operational_week_cdmx
from utils.logger import ok, info, err


# Una semana tarda ~25-40 min; el Job tiene task-timeout=2h.
MAX_SEMANAS_CLOUD_RUN = 2


def _is_cloud_run() -> bool:
    return any(k in os.environ for k in ("CLOUD_RUN_JOB", "K_SERVICE", "CLOUD_RUN_EXECUTION"))


# --------------------------------------------------------------------------
# CLI: modo reposición
# --------------------------------------------------------------------------
def _fecha(valor: str) -> date:
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"fecha inválida {valor!r}; formato esperado YYYY-MM-DD")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.pipeline_Pasos",
        description="Reporte de Pasos. Sin flags: última semana operativa cerrada.",
    )
    parser.add_argument("--desde", type=_fecha,
                        help="Lunes de inicio (YYYY-MM-DD), 03:20 CDMX implícito.")
    parser.add_argument("--hasta", type=_fecha,
                        help="Lunes de cierre, EXCLUSIVO (YYYY-MM-DD), 03:20 CDMX implícito.")
    args = parser.parse_args(argv)
    if (args.desde is None) != (args.hasta is None):
        parser.error("--desde y --hasta van juntos (o ninguno).")
    return args


def _lunes_320(d: date) -> datetime:
    return CDMX_TZ.localize(datetime(d.year, d.month, d.day, 3, 20, 0))


def _ventanas_reposicion(desde: date, hasta: date) -> list[tuple[datetime, datetime]]:
    """Parte [desde 03:20, hasta 03:20) en semanas operativas. Falla en el segundo 1."""
    for flag, d in (("--desde", desde), ("--hasta", hasta)):
        if d.weekday() != 0:
            raise RuntimeError(f"pipeline_Pasos: {flag} {d} no es lunes.")
    if desde >= hasta:
        raise RuntimeError(f"pipeline_Pasos: --desde {desde} debe ser anterior a --hasta {hasta}.")

    _, ultimo_cierre = last_completed_operational_week_cdmx()
    if _lunes_320(hasta) > ultimo_cierre:
        raise RuntimeError(
            f"pipeline_Pasos: --hasta {hasta} es posterior al último cierre "
            f"operativo ({ultimo_cierre:%Y-%m-%d %H:%M}); esa semana no ha terminado."
        )

    n_semanas = (hasta - desde).days // 7
    if _is_cloud_run() and n_semanas > MAX_SEMANAS_CLOUD_RUN:
        raise RuntimeError(
            f"pipeline_Pasos: {n_semanas} semanas exceden el tope de "
            f"{MAX_SEMANAS_CLOUD_RUN} por ejecución en Cloud Run (task-timeout 2h). "
            f"Partir en varias ejecuciones."
        )

    # Pasar now = lunes-03:20 de cierre hace que el helper devuelva exactamente esa semana.
    return [
        last_completed_operational_week_cdmx(now=_lunes_320(desde + timedelta(days=7 * k)))
        for k in range(1, n_semanas + 1)
    ]


def _assert_no_existen_en_drive(drive_root: str,
                                ventanas: list[tuple[datetime, datetime]]) -> None:
    """Modo reposición: el loader OMITE por nombre, así que los CSV erróneos
    deben haberse borrado a mano. Si alguno sigue en Drive, abortar ANTES de
    los ~40 min de scraping por semana.
    """
    loader = Pasos_load_to_drive(file_paths=[], root_folder_id=drive_root,
                                 year=ventanas[0][0].isocalendar()[0],
                                 week_number=ventanas[0][0].isocalendar()[1])
    service = loader._get_drive_service()

    pendientes: list[str] = []
    for start, end in ventanas:
        iso_year, iso_week = _iso_destino(start)
        year_id = loader._find_folder(service, _year_folder_name(iso_year), drive_root)
        week_id = year_id and loader._find_folder(service, _week_folder_name(iso_week), year_id)
        if not week_id:
            continue
        for linea in LINEAS_OPERATIVAS:
            nombre = nombre_csv(LINEA_SLUG[linea], start, end)
            if loader._file_already_exists(service, nombre, week_id):
                pendientes.append(f"{iso_year:04d}/{iso_week:02d}/{nombre}")

    if pendientes:
        raise RuntimeError(
            f"pipeline_Pasos: {len(pendientes)} archivo(s) a reponer siguen en Drive "
            f"y el loader los omitiría. Bórralos a mano y re-ejecuta:\n  "
            + "\n  ".join(pendientes)
        )
    ok("Pre-chequeo Drive: ningún archivo a reponer existe todavía")


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

    La carrera con el nombre del archivo quedó cerrada: main() pasa la misma
    ventana a Pasos_Scraper.scrape(window), que deriva 'sem{N}' de este start.
    """
    iso_year, iso_week, _ = start.isocalendar()
    return iso_year, iso_week


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def _procesar_semana(scraper: Pasos_Scraper, drive_root: str,
                     start: datetime, end: datetime) -> None:
    """Scrape + carga de UNA semana operativa [start, end)."""
    iso_year, iso_week = _iso_destino(start)

    info(f"Semana operativa: {start.strftime('%Y-%m-%d %H:%M %Z')} .. "
         f"{end.strftime('%Y-%m-%d %H:%M %Z')}")
    info(f"Destino en Drive: reporte_de_pasos/{iso_year:04d}/{iso_week:02d}")
    info(f"Líneas operativas a procesar: {len(LINEAS_OPERATIVAS)}")

    # 1. Extract: scrape de las 10 líneas
    info("─ Etapa 1/2: Extract ─────────────────────────────────")
    raw_csvs = scraper.scrape(window=(start, end))

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
    ok(f"  pipeline_Pasos: semana operativa "
       f"{start.strftime('%Y-%m-%d %H:%M')}..{end.strftime('%Y-%m-%d %H:%M')} "
       f"completada")


def main(argv: list[str] | None = None) -> int:
    info("=" * 60)
    info("  pipeline_Pasos — arranque")
    info("=" * 60)

    args = _parse_args(argv)

    # 0. Validar configuración ANTES de abrir Chrome
    drive_root = _validate_env()

    # Semana(s) operativa(s): lunes 03:20 → lunes 03:20 CDMX.
    # UNA sola lectura del reloj por ventana; año y semana se derivan de su `start`.
    if args.desde is None:
        ventanas = [last_completed_operational_week_cdmx()]
    else:
        ventanas = _ventanas_reposicion(args.desde, args.hasta)
        info(f"MODO REPOSICIÓN: {len(ventanas)} semana(s), "
             f"{args.desde} 03:20 .. {args.hasta} 03:20 CDMX")
        _assert_no_existen_en_drive(drive_root, ventanas)

    # Best-effort por semana + raise final (misma política que el loader por archivo).
    scraper = Pasos_Scraper()
    fallas: list[tuple[datetime, Exception]] = []
    for idx, (start, end) in enumerate(ventanas, start=1):
        info("=" * 60)
        info(f"  Semana {idx}/{len(ventanas)}")
        info("=" * 60)
        try:
            _procesar_semana(scraper, drive_root, start, end)
        except Exception as exc:
            err(f"Semana {start:%Y-%m-%d} falló: {type(exc).__name__}: {exc}")
            fallas.append((start, exc))

    if fallas:
        if len(ventanas) == 1:
            raise fallas[0][1]
        detalle = "; ".join(f"{s:%Y-%m-%d}: {type(e).__name__}" for s, e in fallas)
        raise RuntimeError(
            f"pipeline_Pasos: {len(fallas)}/{len(ventanas)} semanas fallaron. {detalle}"
        ) from fallas[0][1]

    info("=" * 60)
    ok(f"  pipeline_Pasos: {len(ventanas)} semana(s) completada(s)")
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