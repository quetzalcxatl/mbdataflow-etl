# pipelines/pipeline_PV.py
# -*- coding: utf-8 -*-
"""
Orquestador del pipeline de Sonda PV (Programado vs Real por jornada).

Reemplaza al job legacy `mbdataflow_2` (WRITE_APPEND sin idempotencia).

Ejecución:
    python -m pipelines.pipeline_PV
    python -m pipelines.pipeline_PV --dry-run     # extract + transform, sin BQ

Grafo de dependencias:

    scrape (Sonda -> 2 CSV crudos: Matutino y Vespertino del día)
        │
        └── transform(raw) -> PV_<YYYYMMDD>.csv (ambos turnos, orden del schema)
                │
                └── bq_load -> Sonda.PV   (delete-then-append por día)

Corre una vez al día, 16:00 CDMX, cuando el turno Vespertino ya es consultable
(misma hora que el job legacy, para continuidad del histórico).

Idempotencia: BigQueryDayLoader borra DATE(date) = hoy y reinserta ambos turnos.
Reejecutar el mismo día (reintento de Cloud Run, corrida manual) no duplica.
Antes del DELETE exige que el CSV traiga SOLO la fecha de hoy y AMBOS turnos.

Fecha: una sola lectura del reloj (today_cdmx) compartida por las tres etapas
(§5.5). En Cloud Run el reloj es UTC; datetime.now() daría el día equivocado
en corridas nocturnas.

Fallos (§5.6): todo propaga con exit code != 0. Validación de env vars al inicio.
"""

from __future__ import annotations

import argparse
import sys

from config.settings import (
    BQ_PROJECT,
    BQ_DATASET_SONDA,
    BQ_TABLE_PV,
    SONDA_QUERY_USER,
    SONDA_QUERY_PASSWORD,
)
from extract.scrapers.Sonda_PV import SondaPV_Scraper, TURNOS
from transform.transformers.Sonda_PV import transform
from load.loaders.BigQuery_day_loader import BigQueryDayLoader
from load.schemas.sonda_pv import PV_SCHEMA
from utils.dates import today_cdmx
from utils.logger import ok, info, err


# --------------------------------------------------------------------------
# Validación temprana de configuración
# --------------------------------------------------------------------------
def _validate_env(dry_run: bool) -> None:
    """Verifica la configuración ANTES de abrir Chrome."""
    requeridas = [
        ("SONDA_QUERY_USER",     SONDA_QUERY_USER),
        ("SONDA_QUERY_PASSWORD", SONDA_QUERY_PASSWORD),
    ]
    if not dry_run:
        requeridas += [
            ("BQ_PROJECT",       BQ_PROJECT),
            ("BQ_DATASET_SONDA", BQ_DATASET_SONDA),
        ]
    faltantes = [var for var, val in requeridas if not val]
    if faltantes:
        raise RuntimeError(
            "pipeline_PV: configuración incompleta. Faltan env vars: "
            + ", ".join(faltantes)
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(dry_run: bool = False) -> int:
    info("=" * 60)
    info(f"  pipeline_PV — arranque{' (DRY RUN)' if dry_run else ''}")
    info("=" * 60)

    # 0. Validar configuración ANTES de abrir Chrome
    _validate_env(dry_run)

    # UNA sola lectura del reloj para las tres etapas
    fecha = today_cdmx().date()
    info(f"Fecha de carga: {fecha}")

    # 1. Extract: ambos turnos del día
    info("─ Etapa 1/3: Extract ─────────────────────────────────")
    raw_csvs = SondaPV_Scraper(fecha).scrape()
    if set(raw_csvs) != set(TURNOS):
        raise RuntimeError(
            f"pipeline_PV: el scraper devolvió turnos {sorted(raw_csvs)}, "
            f"se esperaban {sorted(TURNOS)}."
        )
    ok(f"Extract completo: {[p.name for p in raw_csvs.values()]}")

    # 2. Transform: un CSV con ambos turnos
    info("─ Etapa 2/3: Transform ───────────────────────────────")
    pv_csv = transform(raw_csvs, fecha)
    ok(f"Transform completo: {pv_csv.name}")

    if dry_run:
        info(f"DRY RUN: se omite la carga a {BQ_TABLE_PV}. CSV procesado: {pv_csv}")
        return 0

    # 3. BQ Load: delete-then-append del día
    info("─ Etapa 3/3: BQ Load -> Sonda.PV ─────────────────────")
    BigQueryDayLoader(
        csv_path=pv_csv,
        table_id=BQ_TABLE_PV,
        schema=PV_SCHEMA,
        date_column="date",
        date_column_type="DATETIME",
        load_date=fecha,
        partition_column="turno",
        expected_partitions=set(TURNOS),
    ).run()

    info("=" * 60)
    ok(f"  pipeline_PV: {fecha} completada")
    info("=" * 60)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pipeline_PV: Sonda PV -> BigQuery")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract + transform; omite la carga a BigQuery")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        sys.exit(main(dry_run=args.dry_run))
    except Exception as e:
        # §5.6: cualquier fallo NO capturado explícitamente propaga con exit != 0.
        # Cloud Run lo reporta FAILED y la alerta de Cloud Monitoring dispara.
        err(f"pipeline_PV FALLÓ: {type(e).__name__}: {e}")
        raise
