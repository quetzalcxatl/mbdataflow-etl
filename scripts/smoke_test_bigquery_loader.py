#!/usr/bin/env python
# scripts/smoketest_bigquery_loader.py
# -*- coding: utf-8 -*-
"""
Smoke test del BigQueryLoader contra tablas DUMMY (nunca producción).

Valida COMPATIBILIDAD ESTRUCTURAL y SEMÁNTICA DEL DELETE antes de apuntar el
loader a las tablas reales. NO valida corrección de los datos — eso es la
validación de paridad contra los notebooks legacy.

PRECONDICIONES:
  1. Existen las tablas dummy, creadas con CREATE TABLE ... LIKE.
  2. La SA (o tu cuenta) tiene bigquery.jobUser + bigquery.dataEditor.
  3. Tienes CSVs de scrape+transform de DOS semanas DISTINTAS (ver CONFIG).
     La segunda semana es indispensable para el check de selectividad.
  4. pip install db-dtypes  (lo necesita to_dataframe del check de round-trip)

QUÉ VALIDA (6 checks):
  1. Encaje de schema      — el load job no falla por tipo/columna/orden.
  2. Conteo                — filas del CSV == filas en la tabla tras cargar.
  3. Idempotencia          — recargar la MISMA semana deja el mismo conteo.
  4. Guarda de rango       — un CSV fuera del rango declarado ABORTA sin DELETE.
  5. Round-trip tipos      — columnas críticas sobreviven el viaje a BQ y vuelta.
  6. Selectividad de rango — cargar la semana B NO borra la semana A.  ← CRÍTICO

Sobre el check 6: los checks 2 y 3 truncan la tabla, así que ésta nunca
contiene más de una semana y NO ejercen la propiedad central del
delete-then-append sobre una tabla histórica (que el DELETE sea acotado).
El check 6 existe para cerrar ese hueco: acumula dos semanas y verifica que
recargar una no toca la otra.

Para verificar que la semana A sobrevive se usa una formulación SQL
DELIBERADAMENTE DISTINTA a la del loader (DATE(col) BETWEEN, en vez del
rango half-open sobre TIMESTAMP/DATETIME). Si ambos caminos coinciden, el
resultado es cross-validación real; si el test copiara el predicado del
loader, un predicado equivocado pasaría el check en falso.

NO hace DROP de las dummies al terminar: si un check falla, quieres
inspeccionar el estado en la consola.

Uso:
    python -m scripts.smoketest_bigquery_loader
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

_PROC = Path("data/processed/processed_Viaje")

# DOS semanas distintas. La A es la que usan los checks 1-5; el check 6 usa
# ambas. Deben ser semanas NO adyacentes-solapadas y con CSVs ya generados.
WEEK_A = {
    "range": (date(2026, 7, 13), date(2026, 7, 19)),
    "viaje": _PROC / "VIAJE" / "VIAJE_130726_190726.csv",
    "intervalos": _PROC / "INTERVALOS_Y_CUMPLIMIENTOS"
                        / "INTERVALOS_Y_CUMPLIMIENTOS_130726_190726.csv",
}
WEEK_B = {
    "range": (date(2026, 7, 6), date(2026, 7, 12)),
    "viaje": _PROC / "VIAJE" / "VIAJE_060726_120726.csv",
    "intervalos": _PROC / "INTERVALOS_Y_CUMPLIMIENTOS"
                        / "INTERVALOS_Y_CUMPLIMIENTOS_060726_120726.csv",
}

# Tipo de la columna de fecha en cada tabla destino (afecta el cast del DELETE).
DATE_COL = "FECHA"
VIAJE_DATE_TYPE = "TIMESTAMP"       # Sonda.VIAJES.FECHA
INTERVALOS_DATE_TYPE = "DATETIME"   # TIEMPO_INTERTRAMOS...FECHA


# ==========================================================================
# Utilidades
# ==========================================================================
def _client() -> bigquery.Client:
    creds, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(credentials=creds, project=project)


def _count(client: bigquery.Client, table_id: str) -> int:
    """COUNT(*) total — get_table().num_rows tiene lag de metadata post-load."""
    rows = client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`").result()
    return list(rows)[0]["n"]


def _count_in_range(
    client: bigquery.Client, table_id: str, date_column: str,
    rng: tuple[date, date],
) -> int:
    """
    Cuenta filas de un rango con una formulación INDEPENDIENTE de la del loader.

    El loader usa:  col >= CAST(@start) AND col < CAST(DATE_ADD(@end, 1 DAY))
    Aquí usamos:    DATE(col) BETWEEN @start AND @end

    Son caminos distintos al mismo resultado. Si coinciden, el predicado del
    loader queda cross-validado; si el test copiara el predicado, un error en
    él sería invisible.
    """
    query = f"""
        SELECT COUNT(*) AS n FROM `{table_id}`
        WHERE DATE({date_column}) BETWEEN @start AND @end
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start", "DATE", rng[0]),
            bigquery.ScalarQueryParameter("end", "DATE", rng[1]),
        ]
    )
    return list(client.query(query, job_config=cfg).result())[0]["n"]


def _truncate(client: bigquery.Client, table_id: str) -> None:
    client.query(f"TRUNCATE TABLE `{table_id}`").result()


def _load(csv_path: Path, table_id: str, schema, date_type: str,
          rng: tuple[date, date]) -> int:
    """Instancia y corre el loader. Atajo para no repetir kwargs."""
    return BigQueryLoader(
        csv_path=csv_path,
        table_id=table_id,
        schema=schema,
        date_column=DATE_COL,
        date_column_type=date_type,
        date_range=rng,
    ).run()


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
            print(f"  {'✓' if p else '✗'} {name}" + (f" — {detail}" if detail else ""))
        return passed == total


# ==========================================================================
# Checks
# ==========================================================================
def check_load_and_count(
    client: bigquery.Client, rep: Reporter, csv_path: Path, dummy_id: str,
    schema, date_type: str, rng: tuple[date, date], label: str,
) -> bool:
    """Checks 1 y 2. Trunca primero para partir de estado conocido."""
    _truncate(client, dummy_id)

    csv_rows = len(pd.read_csv(csv_path, low_memory=False))
    try:
        inserted = _load(csv_path, dummy_id, schema, date_type, rng)
    except Exception as e:
        rep.record(f"{label}: encaje de schema", False, f"load falló: {type(e).__name__}: {e}")
        return False

    rep.record(f"{label}: encaje de schema", True, "load job aceptó el CSV")

    table_rows = _count(client, dummy_id)
    ok_count = (table_rows == csv_rows == inserted)
    rep.record(
        f"{label}: conteo", ok_count,
        f"CSV={csv_rows}, insertadas={inserted}, en tabla={table_rows}",
    )
    return True


def check_idempotency(
    client: bigquery.Client, rep: Reporter, csv_path: Path, dummy_id: str,
    schema, date_type: str, rng: tuple[date, date], label: str,
) -> None:
    """Check 3: recargar la MISMA semana no duplica."""
    before = _count(client, dummy_id)
    try:
        _load(csv_path, dummy_id, schema, date_type, rng)
    except Exception as e:
        rep.record(f"{label}: idempotencia", False, f"2da corrida lanzó: {e}")
        return

    after = _count(client, dummy_id)
    passed = (before == after)
    rep.record(
        f"{label}: idempotencia", passed,
        f"antes={before}, después={after}" + ("" if passed else "  ← DUPLICÓ"),
    )


def check_range_guard(
    client: bigquery.Client, rep: Reporter, csv_path: Path, dummy_id: str,
    schema, date_type: str, label: str,
) -> None:
    """Check 4: rango declarado que no contiene las fechas del CSV → aborta."""
    wrong_range = (date(2020, 1, 6), date(2020, 1, 12))
    before = _count(client, dummy_id)
    try:
        _load(csv_path, dummy_id, schema, date_type, wrong_range)
        rep.record(f"{label}: guarda de rango", False, "NO abortó con rango equivocado")
    except RuntimeError:
        after = _count(client, dummy_id)
        no_delete = (before == after)
        rep.record(
            f"{label}: guarda de rango", no_delete,
            "abortó sin tocar la tabla" if no_delete
            else f"abortó PERO el conteo cambió ({before}→{after})",
        )
    except Exception as e:
        rep.record(
            f"{label}: guarda de rango", False,
            f"lanzó {type(e).__name__}, esperaba RuntimeError",
        )


def check_roundtrip_types(
    client: bigquery.Client, rep: Reporter, csv_path: Path, dummy_id: str,
    label: str, critical_cols: list[str],
) -> None:
    """Check 5: columnas críticas sobreviven el round-trip CSV→BQ→pandas."""
    df_csv = pd.read_csv(csv_path, low_memory=False)
    df_bq = client.query(f"SELECT * FROM `{dummy_id}`").result().to_dataframe()

    all_ok = True
    details = []
    for col in critical_cols:
        if col not in df_csv.columns or col not in df_bq.columns:
            all_ok = False
            details.append(f"{col}: ausente")
            continue
        n_csv, n_bq = len(df_csv[col].dropna()), len(df_bq[col].dropna())
        if n_csv != n_bq:
            all_ok = False
            details.append(f"{col}: nulos difieren (csv {n_csv} vs bq {n_bq})")
        else:
            details.append(f"{col}: {n_csv} valores ✓")

    rep.record(f"{label}: round-trip tipos", all_ok, "; ".join(details))


def check_range_selectivity(
    client: bigquery.Client, rep: Reporter, dummy_id: str, schema,
    date_type: str, label: str,
    csv_a: Path, range_a: tuple[date, date],
    csv_b: Path, range_b: tuple[date, date],
) -> None:
    """
    Check 6 (CRÍTICO): el DELETE está ACOTADO al rango declarado.

    Los checks 2-3 truncan la tabla, así que nunca contiene dos semanas y no
    prueban que el DELETE sea selectivo. Éste sí:

      1. TRUNCATE.
      2. Cargar semana A            → tabla == filas(A)
      3. Cargar semana B            → tabla == filas(A) + filas(B)   ← ACUMULA
      4. RECARGAR semana B          → tabla sigue == filas(A)+filas(B)
      5. Verificar A intacta        → count_in_range(A) == filas(A)  ← SELECTIVO

    Si el paso 3 diera filas(B) en vez de la suma, el loader estaría haciendo
    la tabla un SNAPSHOT semanal en vez de un histórico acumulativo, y el
    predicado del DELETE tendría que revisarse.
    """
    _truncate(client, dummy_id)

    rows_a = len(pd.read_csv(csv_a, low_memory=False))
    rows_b = len(pd.read_csv(csv_b, low_memory=False))

    try:
        # 2. Semana A
        _load(csv_a, dummy_id, schema, date_type, range_a)
        after_a = _count(client, dummy_id)
        if after_a != rows_a:
            rep.record(
                f"{label}: selectividad de rango", False,
                f"carga inicial de A inconsistente: esperaba {rows_a}, hay {after_a}",
            )
            return

        # 3. Semana B — debe ACUMULAR, no reemplazar
        _load(csv_b, dummy_id, schema, date_type, range_b)
        after_b = _count(client, dummy_id)
        esperado = rows_a + rows_b
        if after_b != esperado:
            rep.record(
                f"{label}: selectividad de rango", False,
                f"NO ACUMULÓ: tras cargar B esperaba {esperado} (A={rows_a}+B={rows_b}), "
                f"hay {after_b}. El DELETE borró fuera de su rango → tabla snapshot.",
            )
            return

        # 4. Recargar B — el DELETE debe tocar SOLO B
        _load(csv_b, dummy_id, schema, date_type, range_b)
        after_reload = _count(client, dummy_id)

        # 5. Verificar A intacta, con predicado INDEPENDIENTE del loader
        a_final = _count_in_range(client, dummy_id, DATE_COL, range_a)
        b_final = _count_in_range(client, dummy_id, DATE_COL, range_b)

    except Exception as e:
        rep.record(f"{label}: selectividad de rango", False, f"{type(e).__name__}: {e}")
        return

    passed = (after_reload == esperado and a_final == rows_a and b_final == rows_b)
    rep.record(
        f"{label}: selectividad de rango", passed,
        f"total={after_reload} (esperado {esperado}); "
        f"A={a_final}/{rows_a}, B={b_final}/{rows_b}"
        + ("" if passed else "  ← el DELETE se salió de su rango"),
    )


# ==========================================================================
# Main
# ==========================================================================
def _run_suite(
    client: bigquery.Client, rep: Reporter, dummy_id: str, schema,
    date_type: str, label: str, critical_cols: list[str],
) -> None:
    a, b = WEEK_A, WEEK_B
    key = "viaje" if label == "VIAJE" else "intervalos"
    csv_a, csv_b = a[key], b[key]

    loaded = check_load_and_count(
        client, rep, csv_a, dummy_id, schema, date_type, a["range"], label
    )
    if not loaded:
        return

    check_idempotency(client, rep, csv_a, dummy_id, schema, date_type, a["range"], label)
    check_range_guard(client, rep, csv_a, dummy_id, schema, date_type, label)
    check_roundtrip_types(client, rep, csv_a, dummy_id, label, critical_cols)
    check_range_selectivity(
        client, rep, dummy_id, schema, date_type, label,
        csv_a, a["range"], csv_b, b["range"],
    )


def main() -> int:
    client = _client()

    print("\n" + "=" * 60)
    print("  SMOKE TEST — BigQueryLoader contra dummies")
    print("=" * 60)
    print(f"  Proyecto: {PROJECT} · Dataset pruebas: {DATASET_PRUEBAS}")
    print(f"  Semana A: {WEEK_A['range'][0]} .. {WEEK_A['range'][1]}")
    print(f"  Semana B: {WEEK_B['range'][0]} .. {WEEK_B['range'][1]}\n")

    rep = Reporter()

    print("── VIAJE " + "─" * 50)
    _run_suite(
        client, rep, VIAJE_DUMMY, VIAJES_SCHEMA, VIAJE_DATE_TYPE, "VIAJE",
        ["FECHA", "PARTIDA_REAL", "STATUS_DEL_VIAJE"],
    )

    print("── INTERVALOS_Y_CUMPLIMIENTOS " + "─" * 30)
    _run_suite(
        client, rep, INTERVALOS_DUMMY, INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA,
        INTERVALOS_DATE_TYPE, "INTERVALOS",
        ["FECHA", "PARTIDA_REAL", "ECONOMICO", "INTERVALO"],
    )

    all_passed = rep.summary()

    print("\n  Las dummies NO se limpiaron (para inspección).")
    print("  Quedan con las semanas A y B cargadas tras el check 6.")
    print(f"  Para vaciarlas: TRUNCATE TABLE `{VIAJE_DUMMY}`; y la de intervalos.\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())