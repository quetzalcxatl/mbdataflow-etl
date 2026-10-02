#!/usr/bin/env python
# scripts/smoke_test_pv_loader.py
# -*- coding: utf-8 -*-
"""
Smoke test del BigQueryDayLoader para pipeline_PV contra una tabla DUMMY
(nunca producción).

PRECONDICIONES:
  1. Existe la dummy, creada desde producción (mismo schema):
       CREATE TABLE `centrodecontrol.pruebas.PV_smoketest`
       LIKE `centrodecontrol.Sonda.PV`;
  2. Tienes un CSV procesado real, generado con:
       python -m pipelines.pipeline_PV --dry-run
     (queda en data/processed/processed_PV/PV_<YYYYMMDD>.csv). Pásalo como
     argumento o se toma el más reciente.

Solo necesita UN día real: el día B del check de selectividad se genera
copiando el CSV y cambiando la fecha.

QUÉ VALIDA (6 checks):
  1. Encaje de schema      — el load job acepta el CSV (orden y tipos).
  2. Conteo                — filas del CSV == filas en la tabla.
  3. Idempotencia          — recargar el MISMO día deja el mismo conteo.
  4. Guarda de fecha       — un CSV con otra fecha ABORTA sin DELETE.
  5. Guarda de turnos      — un CSV con un solo turno ABORTA sin DELETE.
  6. Selectividad          — cargar el día B NO borra el día A.  ← CRÍTICO

Para verificar que el día A sobrevive se usa un predicado DISTINTO al del
loader (rango sobre la columna, en vez de DATE(col) = @d): si ambos caminos
coinciden, el resultado es cross-validación real.

NO hace DROP de la dummy al terminar (para inspección).

Uso:
    python -m scripts.smoke_test_pv_loader [ruta/al/PV_YYYYMMDD.csv]
"""

from __future__ import annotations

import glob
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import google.auth
import pandas as pd
from google.cloud import bigquery

from config.settings import BQ_TABLE_PV_TEST, PROCESSED_PV_PATH
from load.loaders.BigQuery_day_loader import BigQueryDayLoader
from load.schemas.sonda_pv import PV_SCHEMA

TURNOS = {"Matutino", "Vespertino"}


# ==========================================================================
# Utilidades
# ==========================================================================
def _client() -> bigquery.Client:
    creds, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(credentials=creds, project=project)


def _count(client: bigquery.Client, where: str = "TRUE", params=None) -> int:
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    q = f"SELECT COUNT(*) AS n FROM `{BQ_TABLE_PV_TEST}` WHERE {where}"
    return list(client.query(q, job_config=cfg).result())[0]["n"]


def _count_day(client: bigquery.Client, d: date) -> int:
    """Predicado INDEPENDIENTE del loader: rango [d, d+1) sobre la columna."""
    start = datetime(d.year, d.month, d.day)
    return _count(
        client, "`date` >= @s AND `date` < @e",
        [bigquery.ScalarQueryParameter("s", "DATETIME", start),
         bigquery.ScalarQueryParameter("e", "DATETIME", start + timedelta(days=1))],
    )


def _truncate(client: bigquery.Client) -> None:
    client.query(f"TRUNCATE TABLE `{BQ_TABLE_PV_TEST}`").result()


def _load(csv_path: Path, d: date) -> int:
    return BigQueryDayLoader(
        csv_path=csv_path,
        table_id=BQ_TABLE_PV_TEST,
        schema=PV_SCHEMA,
        date_column="date",
        date_column_type="DATETIME",
        load_date=d,
        partition_column="turno",
        expected_partitions=TURNOS,
    ).run()


def _csv_date(csv_path: Path) -> date:
    fechas = pd.to_datetime(pd.read_csv(csv_path, usecols=["date"])["date"])
    return fechas.dt.date.iloc[0]


def _variant(src: Path, out_dir: Path, name: str, *, new_date: date | None = None,
             only_turno: str | None = None) -> Path:
    """Copia del CSV con otra fecha y/o un solo turno (para checks 4-6)."""
    df = pd.read_csv(src, dtype=str)
    if new_date is not None:
        df["date"] = f"{new_date.isoformat()} 00:00:00"
    if only_turno is not None:
        df = df[df["turno"] == only_turno]
    out = out_dir / name
    df.to_csv(out, index=False)
    return out


class Reporter:
    """Acumula resultados y los imprime al final. No aborta en fallo."""

    def __init__(self):
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append((name, passed, detail))
        mark = "✓ PASA" if passed else "✗ FALLA"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))

    def summary(self) -> bool:
        passed = sum(1 for _, p, _ in self.results if p)
        print("\n" + "=" * 60)
        print(f"  RESULTADO: {passed}/{len(self.results)} checks pasaron")
        print("=" * 60)
        return passed == len(self.results)


def _expect_abort(client, rep: Reporter, name: str, csv_path: Path, d: date) -> None:
    """El loader debe lanzar RuntimeError SIN tocar la tabla."""
    before = _count(client)
    try:
        _load(csv_path, d)
        rep.record(name, False, "NO abortó")
    except RuntimeError:
        after = _count(client)
        rep.record(name, before == after,
                   "abortó sin tocar la tabla" if before == after
                   else f"abortó PERO el conteo cambió ({before}→{after})")
    except Exception as e:
        rep.record(name, False, f"lanzó {type(e).__name__}, esperaba RuntimeError")


# ==========================================================================
# Main
# ==========================================================================
def main() -> int:
    if len(sys.argv) > 1:
        csv_a = Path(sys.argv[1])
    else:
        matches = glob.glob(str(PROCESSED_PV_PATH / "PV_*.csv"))
        if not matches:
            raise SystemExit(f"No hay CSV procesado en {PROCESSED_PV_PATH}. "
                             f"Corre antes: python -m pipelines.pipeline_PV --dry-run")
        csv_a = Path(max(matches, key=os.path.getmtime))

    day_a = _csv_date(csv_a)
    day_b = day_a - timedelta(days=1)
    rows_a = len(pd.read_csv(csv_a))

    client = _client()
    rep = Reporter()

    print("\n" + "=" * 60)
    print("  SMOKE TEST — BigQueryDayLoader (pipeline_PV)")
    print("=" * 60)
    print(f"  Dummy: {BQ_TABLE_PV_TEST}")
    print(f"  CSV A: {csv_a.name} ({rows_a} filas, día {day_a}) · día B sintético: {day_b}\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        csv_b = _variant(csv_a, tmp_dir, "PV_B.csv", new_date=day_b)
        csv_one_turno = _variant(csv_a, tmp_dir, "PV_matutino.csv", only_turno="Matutino")

        # 1-2. Encaje de schema + conteo
        _truncate(client)
        try:
            inserted = _load(csv_a, day_a)
            rep.record("encaje de schema", True, "load job aceptó el CSV")
            in_table = _count(client)
            rep.record("conteo", inserted == in_table == rows_a,
                       f"CSV={rows_a}, insertadas={inserted}, en tabla={in_table}")
        except Exception as e:
            rep.record("encaje de schema", False, f"{type(e).__name__}: {e}")
            rep.summary()
            return 1

        # 3. Idempotencia
        before = _count(client)
        _load(csv_a, day_a)
        after = _count(client)
        rep.record("idempotencia", before == after,
                   f"antes={before}, después={after}" + ("" if before == after else "  ← DUPLICÓ"))

        # 4. Guarda de fecha: CSV del día B declarado como día A
        _expect_abort(client, rep, "guarda de fecha", csv_b, day_a)

        # 5. Guarda de turnos: solo Matutino
        _expect_abort(client, rep, "guarda de turnos", csv_one_turno, day_a)

        # 6. Selectividad: A + B acumulan; recargar B no toca A
        try:
            _load(csv_b, day_b)
            total = _count(client)
            _load(csv_b, day_b)
            total_reload = _count(client)
            a_final, b_final = _count_day(client, day_a), _count_day(client, day_b)
            passed = (total == total_reload == 2 * rows_a
                      and a_final == rows_a and b_final == rows_a)
            rep.record("selectividad", passed,
                       f"total={total_reload} (esperado {2 * rows_a}); "
                       f"A={a_final}/{rows_a}, B={b_final}/{rows_a}"
                       + ("" if passed else "  ← el DELETE se salió de su día"))
        except Exception as e:
            rep.record("selectividad", False, f"{type(e).__name__}: {e}")

    all_passed = rep.summary()
    print(f"\n  La dummy NO se limpió. Para vaciarla: TRUNCATE TABLE `{BQ_TABLE_PV_TEST}`;\n")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
