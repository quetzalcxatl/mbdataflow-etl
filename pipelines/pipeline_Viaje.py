# pipelines/pipeline_Viaje.py
# -*- coding: utf-8 -*-
"""
Orquestador del pipeline de Reporte de Viaje.

Encadena las etapas del ETL semanal, aplicando el patrón que ya usan
pipeline_Desinc y pipeline_Circuitos + la capa BigQuery nueva.

Ejecución:
    python -m pipelines.pipeline_Viaje

Grafo de dependencias:

    scrape (Sonda -> CSV crudo, ventana lunes 03:20 → lunes 03:20)
        │
        ├── if DRIVE_BACKUP: drive_load(raw)      [MEDULAR, propaga errores]
        │
        └── transform(raw)  -> (VIAJE.csv, INT_Y_CUMPL.csv)
                │
                ├── bq_load VIAJE  -> Sonda.VIAJES              (delete-then-append)
                │       │
                │       └── sql_runner INTERVALOSDINAMICOS
                │              -> Sonda.INTERVALOS              (CREATE OR REPLACE)
                │
                └── bq_load INT_Y_CUMPL -> TIEMPO_INTERTRAMOS.INTERVALOS_Y_CUMPLIMIENTOS

Dependencia crítica: Sonda.INTERVALOS se reconstruye DESDE Sonda.VIAJES, así que
el sql_runner debe correr DESPUÉS del bq_load de VIAJES. Las dos cargas a BQ
son independientes entre sí; el orden entre ellas no importa, pero por claridad
se hacen en secuencia.

Semana operativa (§ contrato Viaje):
Ver utils.dates.last_completed_operational_week_cdmx — ancla en lunes 03:20 CDMX
para respetar el corte real de servicio de Metrobús. El rango es SEMIABIERTO
[lunes 03:20, próximo lunes 03:20).

Key sintética del loader:
FECHA en ambas tablas es a granularidad de día (siempre midnight). Para hacer
DELETE-THEN-APPEND idempotente contra un rango datetime-preciso, el loader
construye una key sintética DATE(FECHA) + TIME(PARTIDA_REAL). En VIAJES,
PARTIDA_REAL puede ser NULL (viajes cancelados) — el DELETE cae en PARTIDA_PLANEADA
como fallback, y luego en midnight, garantizando idempotencia. En INT_Y_CUMPL
el transform ya filtra a "realizados" así que PARTIDA_REAL casi nunca es NULL
y no requiere fallback.

Fallos (§5.6):
  - Todo lanza excepción y sale con exit code != 0. Nada silencioso — ni siquiera
    Drive backup, que dejó de ser opcional (PR2).
  - Validación de env vars al INICIO: si falta algo, falla en segundo 1 en vez
    de tras 20 minutos de scraping.

Costo aproximado en producción (§8): el CREATE OR REPLACE escanea Sonda.VIAJES
completa cada corrida — deuda de partición conocida, monitorear con el dry-run.
"""

from __future__ import annotations

import os
import sys

from config.settings import (
    BQ_PROJECT,
    BQ_DATASET_PRUEBAS,
    BQ_TABLE_VIAJES_TEST,
    BQ_TABLE_INTERVALOS_TEST,
    BQ_TABLE_INT_CUMPL_TEST,
    DRIVE_VIAJE_FOLDER_ID,
    SQL_INTERVALOSDINAMICOS_PATH,
)
from extract.scrapers.Reporte_Viaje import Viaje_Scraper
from transform.transformers.Reporte_Viaje import transform
from transform.bq_sql_runner import BigQuerySQLRunner
from load.loaders.BigQuery_loader import BigQueryLoader
from load.loaders.Viaje_drive_loader import Viaje_load_to_drive
from load.schemas.viaje import (
    VIAJES_SCHEMA,
    INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA,
)
from utils.dates import last_completed_operational_week_cdmx
from utils.logger import ok, info, err


# --------------------------------------------------------------------------
# Validación temprana de configuración
# --------------------------------------------------------------------------
def _validate_env() -> tuple[bool, str | None]:
    """
    Verifica que la configuración esté completa ANTES de tocar Sonda.

    Falla en el segundo 1 con un mensaje claro; sin esto un env var faltante
    tumbaría el pipeline tras 20 minutos de scraping.

    DRIVE_BACKUP: default "true" (Drive es medular; ver Architecture.md §5.6
    y decisión de diseño PR2). En local, setear DRIVE_BACKUP=false explícitamente
    si no se quiere subir CSVs de prueba a la carpeta de Drive.

    Retorna: (drive_backup_activo, drive_folder_id_o_None).
    """
    faltantes = []
    for var, val in [
        ("BQ_PROJECT",             BQ_PROJECT),
        ("BQ_DATASET_SONDA",       BQ_DATASET_PRUEBAS),
        #("BQ_DATASET_INTERTRAMOS", BQ_DATASET_INTERTRAMOS),
    ]:
        if not val:
            faltantes.append(var)

    drive_backup = os.environ.get("DRIVE_BACKUP", "true").strip().lower() == "true"
    if drive_backup and not DRIVE_VIAJE_FOLDER_ID:
        faltantes.append("DRIVE_RV_FOLDER_ID (requerida cuando DRIVE_BACKUP=true)")

    if faltantes:
        raise RuntimeError(
            "pipeline_Viaje: configuración incompleta. Faltan env vars: "
            + ", ".join(faltantes)
        )

    return drive_backup, DRIVE_VIAJE_FOLDER_ID


# --------------------------------------------------------------------------
# Drive backup — MEDULAR (§5.6)
# --------------------------------------------------------------------------
def _drive_backup(raw_csv, folder_id: str) -> None:
    """
    Sube el CSV crudo a Drive. Parte del camino crítico del pipeline.

    Los errores propagan sin capturar: en Cloud Run un fallo aquí deja el Job
    en FAILED y dispara la alerta de Cloud Monitoring. Es una decisión
    explícita — el respaldo en Drive dejó de ser "por si acaso" para volverse
    parte del contrato del pipeline (Architecture.md §5.6).

    El loader ya distingue transitorio (5xx / 429 / red) de permanente
    (4xx auth/config) y reintenta solo lo transitorio con backoff exponencial
    (2s/4s/8s, 3 intentos). Un blip de red no tumba el Job; una config
    incorrecta sí, en el primer intento.
    """
    file_id = Viaje_load_to_drive(raw_csv, folder_id).run()
    ok(f"Drive backup: {raw_csv.name} -> {file_id}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    info("=" * 60)
    info("  pipeline_Viaje — arranque")
    info("=" * 60)

    # 0. Validar configuración ANTES de abrir Chrome
    drive_backup, drive_folder = _validate_env()

    # Semana operativa: lunes 03:20 → lunes 03:20 CDMX (contrato Viaje)
    start, end = last_completed_operational_week_cdmx()
    info(f"Semana operativa: {start.strftime('%Y-%m-%d %H:%M %Z')} .. "
         f"{end.strftime('%Y-%m-%d %H:%M %Z')}")
    info(f"Drive backup: {'ACTIVO' if drive_backup else 'inactivo'}")

    # 1. Extract: scrape del reporte de Viaje
    info("─ Etapa 1/5: Extract ─────────────────────────────────")
    scraper = Viaje_Scraper()
    raw_csv = scraper.scrape()
    ok(f"Extract completo: {raw_csv.name}")

    # 2. Drive backup (medular; propaga errores)
    if drive_backup:
        info("─ Drive backup ───────────────────────────────────────")
        _drive_backup(raw_csv, drive_folder)

    # 3. Transform: dos CSVs procesados
    info("─ Etapa 2/5: Transform ───────────────────────────────")
    viaje_csv, intcumpl_csv = transform(raw_csv)
    ok(f"Transform completo: {viaje_csv.name}, {intcumpl_csv.name}")

    # 4. BQ Load: VIAJES  (debe ir antes del sql_runner)
    # Key sintética = DATE(FECHA) + TIME(PARTIDA_REAL); fallback PARTIDA_PLANEADA
    # para cubrir cancelados (PARTIDA_REAL NULL) en el DELETE sin perder
    # idempotencia. PARTIDA_REAL es TIME en el schema de VIAJES.
    info("─ Etapa 3/5: BQ Load -> Sonda.VIAJES ─────────────────")
    BigQueryLoader(
        csv_path=viaje_csv,
        table_id=BQ_TABLE_VIAJES_TEST,
        schema=VIAJES_SCHEMA,
        date_column="FECHA",
        date_column_type="TIMESTAMP",
        time_column="PARTIDA_REAL",
        time_column_fallback="PARTIDA_PLANEADA",
        date_range=(start, end),
    ).run()

    # 5. BQ Load: INTERVALOS_Y_CUMPLIMIENTOS  (independiente, orden no crítico)
    # Key sintética = DATE(FECHA) + TIME(PARTIDA_REAL). PARTIDA_REAL en esta
    # tabla es DATETIME con fecha dummy 1900-01-01; TIME() la extrae limpia.
    # El transform ya filtra a "realizados" (Status del Viaje == 1), así que
    # PARTIDA_REAL casi nunca es NULL — no se necesita fallback.
    info("─ Etapa 4/5: BQ Load -> TIEMPO_INTERTRAMOS ───────────")
    BigQueryLoader(
        csv_path=intcumpl_csv,
        table_id=BQ_TABLE_INT_CUMPL_TEST,
        schema=INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA,
        date_column="FECHA",
        date_column_type="DATETIME",
        time_column="PARTIDA_REAL",
        date_range=(start, end),
    ).run()

    # 6. SQL Runner: reconstruye Sonda.INTERVALOS desde Sonda.VIAJES
    info("─ Etapa 5/5: SQL Runner -> Sonda.INTERVALOS ──────────")
    BigQuerySQLRunner(
        sql_path=SQL_INTERVALOSDINAMICOS_PATH,
        source_table=BQ_TABLE_VIAJES_TEST,
        dest_table=BQ_TABLE_INTERVALOS_TEST,
        min_row_ratio=0.9,   # guarda de regresión: histórico solo debe crecer
    ).run()

    info("=" * 60)
    ok(f"  pipeline_Viaje: semana operativa "
       f"{start.strftime('%Y-%m-%d %H:%M')}..{end.strftime('%Y-%m-%d %H:%M')} completada")
    info("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # §5.6: cualquier fallo NO capturado explícitamente propaga con exit != 0.
        # Cloud Run lo reporta FAILED y la alerta de Cloud Monitoring dispara.
        err(f"pipeline_Viaje FALLÓ: {type(e).__name__}: {e}")
        raise