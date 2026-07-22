# pipelines/pipeline_Viaje.py
# -*- coding: utf-8 -*-
"""
Orquestador del pipeline de Reporte de Viaje.

Encadena las etapas del ETL semanal, aplicando el patrón que ya usan
pipeline_Desinc y pipeline_Circuitos + la capa BigQuery nueva.

Ejecución:
    python -m pipelines.pipeline_Viaje

Grafo de dependencias:

    scrape (Sonda -> CSV crudo)
        │
        ├── if DRIVE_BACKUP: drive_load(raw)      [no crítico, warning si falla]
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

Fallos (§5.6):
  - Todo lanza excepción y sale con exit code != 0 salvo el backup a Drive:
    Drive es respaldo, no camino crítico. Se loguea como warning y se sigue.
  - Validación de env vars al INICIO: si falta algo, falla en segundo 1 en vez
    de tras 20 minutos de scraping.

Costo aproximado en producción (§8): el CREATE OR REPLACE escanea Sonda.VIAJES
completa cada corrida — deuda de partición conocida, monitorear con el dry-run.
"""

from __future__ import annotations

import os
import sys
import warnings

from config.settings import (
    BQ_PROJECT,
    BQ_DATASET_SONDA,
    BQ_DATASET_INTERTRAMOS,
    BQ_TABLE_VIAJES,
    BQ_TABLE_INTERVALOS,
    BQ_TABLE_INT_CUMPL,
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
from utils.dates import last_completed_week_cdmx
from utils.logger import ok, info, err


# --------------------------------------------------------------------------
# Validación temprana de configuración
# --------------------------------------------------------------------------
def _validate_env() -> tuple[bool, str | None]:
    """
    Verifica que la configuración esté completa ANTES de tocar Sonda.

    Falla en el segundo 1 con un mensaje claro; sin esto un env var faltante
    tumbaría el pipeline tras 20 minutos de scraping.

    Retorna: (drive_backup_activo, drive_folder_id_o_None).
    """
    faltantes = []
    for var, val in [
        ("BQ_PROJECT",             BQ_PROJECT),
        ("BQ_DATASET_SONDA",       BQ_DATASET_SONDA),
        ("BQ_DATASET_INTERTRAMOS", BQ_DATASET_INTERTRAMOS),
    ]:
        if not val:
            faltantes.append(var)

    drive_backup = os.environ.get("DRIVE_BACKUP", "false").strip().lower() == "true"
    drive_folder = os.environ.get("DRIVE_VIAJE_FOLDER_ID")
    if drive_backup and not drive_folder:
        faltantes.append("DRIVE_VIAJE_FOLDER_ID (requerida cuando DRIVE_BACKUP=true)")

    if faltantes:
        raise RuntimeError(
            "pipeline_Viaje: configuración incompleta. Faltan env vars: "
            + ", ".join(faltantes)
        )

    return drive_backup, drive_folder


# --------------------------------------------------------------------------
# Paso opcional: respaldo del CSV crudo en Drive
# --------------------------------------------------------------------------
def _try_drive_backup(raw_csv, folder_id: str) -> None:
    """
    Sube el CSV crudo a Drive como respaldo auditable. NO crítico.

    Si Drive falla (permisos, cuota, red), se loguea un warning y el pipeline
    continúa. Motivo: la fuente de verdad histórica es Sonda.VIAJES en BQ; el
    Drive es defensa "por si acaso". Un fallo del respaldo no debe derribar
    una carga que después llegó bien a BQ.

    Excepción a esta política: propagaría solo si tras el warning quisiéramos
    darle prioridad; hoy no la tiene.
    """
    try:
        loader = Viaje_load_to_drive(raw_csv, folder_id)
        file_id = loader.run()
        if file_id:
            ok(f"Drive backup: {raw_csv.name} -> {file_id}")
        else:
            warnings.warn(
                f"Drive backup falló silenciosamente para {raw_csv.name} "
                f"(loader retornó None). El pipeline continúa; BQ es la fuente de verdad.",
                RuntimeWarning,
            )
    except Exception as e:
        warnings.warn(
            f"Drive backup falló para {raw_csv.name}: {type(e).__name__}: {e}. "
            f"El pipeline continúa; BQ es la fuente de verdad.",
            RuntimeWarning,
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    info("=" * 60)
    info("  pipeline_Viaje — arranque")
    info("=" * 60)

    # 0. Validar configuración ANTES de abrir Chrome
    drive_backup, drive_folder = _validate_env()
    monday, sunday = last_completed_week_cdmx()
    info(f"Semana a procesar: {monday} .. {sunday}")
    info(f"Drive backup: {'ACTIVO' if drive_backup else 'inactivo'}")

    # 1. Extract: scrape del reporte de Viaje
    info("─ Etapa 1/5: Extract ─────────────────────────────────")
    scraper = Viaje_Scraper()
    raw_csv = scraper.scrape()
    ok(f"Extract completo: {raw_csv.name}")

    # 2. Drive backup (opcional, no crítico)
    if drive_backup:
        info("─ Etapa opcional: Drive backup ───────────────────────")
        _try_drive_backup(raw_csv, drive_folder)

    # 3. Transform: dos CSVs procesados
    info("─ Etapa 2/5: Transform ───────────────────────────────")
    viaje_csv, intcumpl_csv = transform(raw_csv)
    ok(f"Transform completo: {viaje_csv.name}, {intcumpl_csv.name}")

    # 4. BQ Load: VIAJES  (debe ir antes del sql_runner)
    info("─ Etapa 3/5: BQ Load -> Sonda.VIAJES ─────────────────")
    BigQueryLoader(
        csv_path=viaje_csv,
        table_id=BQ_TABLE_VIAJES,
        schema=VIAJES_SCHEMA,
        date_column="FECHA",
        date_column_type="TIMESTAMP",
        date_range=(monday, sunday),
    ).run()

    # 5. BQ Load: INTERVALOS_Y_CUMPLIMIENTOS  (independiente, orden no crítico)
    info("─ Etapa 4/5: BQ Load -> TIEMPO_INTERTRAMOS ───────────")
    BigQueryLoader(
        csv_path=intcumpl_csv,
        table_id=BQ_TABLE_INT_CUMPL,
        schema=INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA,
        date_column="FECHA",
        date_column_type="DATETIME",
        date_range=(monday, sunday),
    ).run()

    # 6. SQL Runner: reconstruye Sonda.INTERVALOS desde Sonda.VIAJES
    info("─ Etapa 5/5: SQL Runner -> Sonda.INTERVALOS ──────────")
    BigQuerySQLRunner(
        sql_path=SQL_INTERVALOSDINAMICOS_PATH,
        source_table=BQ_TABLE_VIAJES,
        dest_table=BQ_TABLE_INTERVALOS,
        min_row_ratio=0.9,   # guarda de regresión: histórico solo debe crecer
    ).run()

    info("=" * 60)
    ok(f"  pipeline_Viaje: semana {monday}..{sunday} completada")
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