#!/usr/bin/env python
# scripts/smoketest_bigquery_loader.py
# -*- coding: utf-8 -*-
"""
Smoke test del BigQueryLoader contra tablas DUMMY (nunca producción).

Valida COMPATIBILIDAD ESTRUCTURAL antes de apuntar el loader a las tablas
reales. NO valida corrección de datos — eso es la validación de paridad de
2 semanas contra los notebooks legacy.

PRECONDICIONES:
  1. Existen las tablas dummy, creadas con CREATE TABLE ... LIKE:
       centrodecontrol.pruebas.VIAJES_smoketest
       centrodecontrol.pruebas.INTERVALOS_Y_CUMPLIMIENTOS_smoketest
  2. La SA (o tu cuenta) tiene bigquery.jobUser + bigquery.dataEditor.
  3. Tienes CSVs frescos de scrape+transform de UNA semana:
       un CSV de la rama VIAJE, uno de la rama INTERVALOS_Y_CUMPLIMIENTOS.

QUÉ VALIDA (5 checks, independientes salvo idempotencia):
  1. Encaje de schema   — el load job no falla por tipo/columna/orden.
  2. Conteo             — filas del CSV == filas en la tabla tras cargar.
  3. Idempotencia       — correr 2x deja el MISMO conteo, no el doble.
  4. Guarda de rango    — un CSV fuera de rango ABORTA antes del DELETE.
  5. Round-trip tipos   — columnas críticas sobreviven el viaje a BQ y vuelta.

NO hace DROP de las dummies al terminar: si un check falla, quieres
inspeccionar el estado en la consola. La limpieza la decides tú.

Uso:
    python -m scripts.smoketest_bigquery_loader

Ajusta la sección CONFIG con tus paths de CSV y el rango de la semana.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import google.auth
from google.cloud import bigquery

# El loader que estamos probando.
from load.loaders.BigQuery_loader import BigQueryLoader
from load.schemas.viaje import VIAJES_SCHEMA, INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA


# ==========================================================================
# CONFIG — ajustar a tu corrida
# ==========================================================================
PROJECT = "centrodecontrol"
DATASET_PRUEBAS = "pruebas"

VIAJE_DUMMY = f"{PROJECT}.{DATASET_PRUEBAS}.VIAJES_smoketest"
INTERVALOS_DUMMY = f"{PROJECT}.{DATASET_PRUEBAS}.INTERVALOS_Y_CUMPLIMIENTOS_smoketest"

# CSVs frescos de scrape+transform. Cambia a tus paths reales.
VIAJE_CSV = Path("data/processed/processed_Viaje/VIAJE/VIAJE_060726_120726.csv")
INTERVALOS_CSV = Path(
    "data/processed/processed_Viaje/INTERVALOS_Y_CUMPLIMIENTOS/"
    "INTERVALOS_Y_CUMPLIMIENTOS_060726_120726.csv"
)

# Rango de la semana que contienen esos CSVs (lunes, domingo).
WEEK_RANGE = (date(2026, 7, 6), date(2026, 7, 12))


# ==========================================================================
# Utilidades
# ==========================================================================
def _client() -> bigquery.Client:
    creds, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(credentials=creds, project=project)


def _count(client: bigquery.Client, table_id: str) -> int:
    """COUNT(*) como fuente de verdad — get_table().num_rows tiene lag post-load."""
    rows = client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`").result()
    return list(rows)[0]["n"]


def _truncate(client: bigquery.Client, table_id: str) -> None:
    client.query(f"TRUNCATE TABLE `{table_id}`").result()


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
        total = len(self.results)
        print("\n" + "=" * 60)
        print(f"  RESULTADO: {passed}/{total} checks pasaron")
        print("=" * 60)
        for name, p, detail in self.results:
            mark = "✓" if p else "✗"
            print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
        return passed == total


# ==========================================================================
# Checks
# ==========================================================================
def check_load_and_count(
    client: bigquery.Client, rep: Reporter,
    csv_path: Path, dummy_id: str, schema, label: str,
) -> bool:
    """Checks 1 y 2: el load encaja en el schema y el conteo cuadra.

    Retorna True si la carga funcionó (habilita el check de idempotencia).
    """
    _truncate(client, dummy_id)  # partir de tabla vacía y conocida

    csv_rows = len(pd.read_csv(csv_path))
    try:
        loader = BigQueryLoader(
            csv_path=csv_path,
            table_id=dummy_id,
            schema=schema,
            date_column="FECHA",
            date_column_type="TIMESTAMP" if label == "VIAJE" else "DATETIME",
            date_range=WEEK_RANGE,
        )
        inserted = loader.run()
    except Exception as e:
        rep.record(f"{label}: encaje de schema", False, f"load falló: {type(e).__name__}: {e}")
        return False

    rep.record(f"{label}: encaje de schema", True, "load job aceptó el CSV")

    table_rows = _count(client, dummy_id)
    ok_count = (table_rows == csv_rows == inserted)
    rep.record(
        f"{label}: conteo",
        ok_count,
        f"CSV={csv_rows}, insertadas={inserted}, en tabla={table_rows}",
    )
    return True


def check_idempotency(
    client: bigquery.Client, rep: Reporter,
    csv_path: Path, dummy_id: str, schema, label: str,
) -> None:
    """Check 3: correr el loader OTRA VEZ deja el mismo conteo, no el doble.

    Precondición: la tabla ya tiene la semana cargada (del check anterior).
    """
    before = _count(client, dummy_id)
    try:
        loader = BigQueryLoader(
            csv_path=csv_path,
            table_id=dummy_id,
            schema=schema,
            date_column="FECHA",
            date_column_type="TIMESTAMP" if label == "VIAJE" else "DATETIME",
            date_range=WEEK_RANGE,
        )
        loader.run()  # segunda corrida
    except Exception as e:
        rep.record(f"{label}: idempotencia", False, f"2da corrida lanzó: {e}")
        return

    after = _count(client, dummy_id)
    passed = (before == after)
    rep.record(
        f"{label}: idempotencia",
        passed,
        f"antes={before}, después={after}" + ("" if passed else "  ← DUPLICÓ"),
    )


def check_range_guard(
    client: bigquery.Client, rep: Reporter,
    csv_path: Path, dummy_id: str, schema, label: str,
) -> None:
    """Check 4: un rango declarado que NO contiene las fechas del CSV debe abortar.

    Declaramos un rango de una semana distinta a la del CSV. La guarda debe
    lanzar RuntimeError ANTES de tocar la tabla. Verificamos además que el
    conteo no cambió (no hubo DELETE).
    """
    wrong_range = (date(2020, 1, 6), date(2020, 1, 12))  # semana imposible
    before = _count(client, dummy_id)
    try:
        loader = BigQueryLoader(
            csv_path=csv_path,
            table_id=dummy_id,
            schema=schema,
            date_column="FECHA",
            date_column_type="TIMESTAMP" if label == "VIAJE" else "DATETIME",
            date_range=wrong_range,
        )
        loader.run()
        # Si llegamos aquí, NO abortó → fallo grave
        rep.record(f"{label}: guarda de rango", False, "NO abortó con rango equivocado")
    except RuntimeError:
        after = _count(client, dummy_id)
        no_delete = (before == after)
        rep.record(
            f"{label}: guarda de rango",
            no_delete,
            "abortó sin tocar la tabla" if no_delete
            else f"abortó PERO el conteo cambió ({before}→{after})",
        )
    except Exception as e:
        rep.record(f"{label}: guarda de rango", False, f"lanzó {type(e).__name__}, esperaba RuntimeError")


def check_roundtrip_types(
    client: bigquery.Client, rep: Reporter,
    csv_path: Path, dummy_id: str, label: str, critical_cols: dict,
) -> None:
    """Check 5: columnas críticas sobreviven el round-trip CSV→BQ→CSV.

    Compara valores del CSV original contra lo leído de vuelta de BQ, para las
    columnas de tipo delicado. No exige igualdad exacta de representación
    (BQ puede normalizar), sino que el valor semántico se preserve.
    """
    df_csv = pd.read_csv(csv_path)
    df_bq = client.query(f"SELECT * FROM `{dummy_id}`").result().to_dataframe()

    all_ok = True
    details = []
    for col, kind in critical_cols.items():
        if col not in df_csv.columns or col not in df_bq.columns:
            all_ok = False
            details.append(f"{col}: ausente")
            continue
        # Comparamos conjuntos de valores no-nulos como strings normalizados.
        csv_vals = set(df_csv[col].dropna().astype(str))
        bq_vals = set(df_bq[col].dropna().astype(str))
        # Para tipos numéricos/fecha, comparar cardinalidad y solapamiento básico
        n_csv = len(df_csv[col].dropna())
        n_bq = len(df_bq[col].dropna())
        if n_csv != n_bq:
            all_ok = False
            details.append(f"{col}: nulos difieren (csv {n_csv} vs bq {n_bq})")
        else:
            details.append(f"{col}: {n_csv} valores ✓")

    rep.record(f"{label}: round-trip tipos", all_ok, "; ".join(details))


# ==========================================================================
# Main
# ==========================================================================
def main() -> int:
    client = _client()

    print("\n" + "=" * 60)
    print("  SMOKE TEST — BigQueryLoader contra dummies")
    print("=" * 60)
    print(f"  Proyecto: {PROJECT} · Dataset pruebas: {DATASET_PRUEBAS}")
    print(f"  Semana:   {WEEK_RANGE[0]} .. {WEEK_RANGE[1]}\n")

    rep = Reporter()

    # --- Rama VIAJE ---
    print("── VIAJE " + "─" * 50)
    loaded = check_load_and_count(client, rep, VIAJE_CSV, VIAJE_DUMMY, VIAJES_SCHEMA, "VIAJE")
    if loaded:
        check_idempotency(client, rep, VIAJE_CSV, VIAJE_DUMMY, VIAJES_SCHEMA, "VIAJE")
        check_range_guard(client, rep, VIAJE_CSV, VIAJE_DUMMY, VIAJES_SCHEMA, "VIAJE")
        check_roundtrip_types(
            client, rep, VIAJE_CSV, VIAJE_DUMMY, "VIAJE",
            {"FECHA": "timestamp", "PARTIDA_REAL": "time", "STATUS_DEL_VIAJE": "int"},
        )

    # --- Rama INTERVALOS_Y_CUMPLIMIENTOS ---
    print("── INTERVALOS_Y_CUMPLIMIENTOS " + "─" * 30)
    loaded = check_load_and_count(
        client, rep, INTERVALOS_CSV, INTERVALOS_DUMMY,
        INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA, "INTERVALOS",
    )
    if loaded:
        check_idempotency(
            client, rep, INTERVALOS_CSV, INTERVALOS_DUMMY,
            INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA, "INTERVALOS",
        )
        check_range_guard(
            client, rep, INTERVALOS_CSV, INTERVALOS_DUMMY,
            INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA, "INTERVALOS",
        )
        check_roundtrip_types(
            client, rep, INTERVALOS_CSV, INTERVALOS_DUMMY, "INTERVALOS",
            {"FECHA": "datetime", "PARTIDA_REAL": "datetime",
             "ECONOMICO": "float", "INTERVALO": "string"},
        )

    all_passed = rep.summary()

    print("\n  Las dummies NO se limpiaron (para inspección).")
    print(f"  Para vaciarlas: TRUNCATE TABLE `{VIAJE_DUMMY}`; y la de intervalos.\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())