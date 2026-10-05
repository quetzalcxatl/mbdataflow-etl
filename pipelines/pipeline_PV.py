# pipelines/pipeline_PV.py
# -*- coding: utf-8 -*-
"""
Orquestador del pipeline de Sonda PV (Programado vs Real por jornada).

Reemplaza al job legacy `mbdataflow_2` (WRITE_APPEND sin idempotencia).

Ejecución:
    python -m pipelines.pipeline_PV                   # carga a pruebas.PV_smoketest
    python -m pipelines.pipeline_PV --target prod     # carga a Sonda.PV
    python -m pipelines.pipeline_PV --dry-run         # extract + transform, sin BQ

Destino (--target)
------------------
El default es `test` (pruebas.PV_smoketest) A PROPÓSITO: una corrida local
que olvide el flag nunca toca producción. El Cloud Run Job pasa
`--target prod` explícito en sus args (scripts/deploy_job_pv.ps1). La tabla
de test es la que usa la auditoría de paridad contra el job legacy
(scripts/audit_pv_parity.py).

Grafo de dependencias:

    scrape (Sonda -> 2 CSV crudos: Matutino y Vespertino del día)
        │
        └── transform(raw) -> PV_<YYYYMMDD>.csv (ambos turnos, orden del schema)
                │
                └── bq_load -> Sonda.PV | pruebas.PV_smoketest  (delete-then-append por día)

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
    BQ_DATASET_PRUEBAS,
    BQ_TABLE_PV,
    BQ_TABLE_PV_TEST,
    SONDA_QUERY_USER,
    SONDA_QUERY_PASSWORD,
)
from extract.scrapers.Sonda_PV import SondaPV_Scraper, TURNOS
from transform.transformers.Sonda_PV import transform
from load.loaders.BigQuery_day_loader import BigQueryDayLoader
from load.schemas.sonda_pv import PV_SCHEMA
from utils.dates import today_cdmx
from utils.logger import ok, info, err


# Tabla destino por --target. Default "test": ver docstring del módulo.
_TARGETS = {
    "test": BQ_TABLE_PV_TEST,
    "prod": BQ_TABLE_PV,
}
DEFAULT_TARGET = "test"


# --------------------------------------------------------------------------
# Validación temprana de configuración
# --------------------------------------------------------------------------
def _validate_env(dry_run: bool, target: str) -> None:
    """Verifica la configuración ANTES de abrir Chrome."""
    requeridas = [
        ("SONDA_QUERY_USER",     SONDA_QUERY_USER),
        ("SONDA_QUERY_PASSWORD", SONDA_QUERY_PASSWORD),
    ]
    if not dry_run:
        requeridas.append(("BQ_PROJECT", BQ_PROJECT))
        if target == "prod":
            requeridas.append(("BQ_DATASET_SONDA", BQ_DATASET_SONDA))
        else:
            requeridas.append(("BQ_DATASET_PRUEBAS", BQ_DATASET_PRUEBAS))
    faltantes = [var for var, val in requeridas if not val]
    if faltantes:
        raise RuntimeError(
            "pipeline_PV: configuración incompleta. Faltan env vars: "
            + ", ".join(faltantes)
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(dry_run: bool = False, target: str = DEFAULT_TARGET) -> int:
    if target not in _TARGETS:
        raise ValueError(f"pipeline_PV: target inválido {target!r}; opciones: {sorted(_TARGETS)}")
    table_id = _TARGETS[target]

    info("=" * 60)
    info(f"  pipeline_PV — arranque{' (DRY RUN)' if dry_run else ''}")
    info(f"  Destino BQ: [{target.upper()}] {table_id}")
    info("=" * 60)

    # 0. Validar configuración ANTES de abrir Chrome
    _validate_env(dry_run, target)

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
        info(f"DRY RUN: se omite la carga a {table_id}. CSV procesado: {pv_csv}")
        return 0

    # 3. BQ Load: delete-then-append del día
    info(f"─ Etapa 3/3: BQ Load -> [{target.upper()}] {table_id} ─────")
    BigQueryDayLoader(
        csv_path=pv_csv,
        table_id=table_id,
        schema=PV_SCHEMA,
        date_column="date",
        date_column_type="DATETIME",
        load_date=fecha,
        partition_column="turno",
        expected_partitions=set(TURNOS),
    ).run()

    info("=" * 60)
    ok(f"  pipeline_PV: {fecha} completada en {table_id}")
    info("=" * 60)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pipeline_PV: Sonda PV -> BigQuery")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract + transform; omite la carga a BigQuery")
    parser.add_argument("--target", choices=sorted(_TARGETS), default=DEFAULT_TARGET,
                        help="Tabla destino: 'test' = pruebas.PV_smoketest (default), "
                             "'prod' = Sonda.PV (solo el Cloud Run Job)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        sys.exit(main(dry_run=args.dry_run, target=args.target))
    except Exception as e:
        # §5.6: cualquier fallo NO capturado explícitamente propaga con exit != 0.
        # Cloud Run lo reporta FAILED y la alerta de Cloud Monitoring dispara.
        err(f"pipeline_PV FALLÓ: {type(e).__name__}: {e}")
        raise
