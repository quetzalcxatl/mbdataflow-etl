#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scripts/audit_pv_parity.py

Auditoría de paridad para pipeline_PV.

Compara la tabla que alimenta el pipeline MIGRADO (corrido con --target test)
contra la tabla que alimenta el job LEGACY en producción (mbdataflow_2):

    pruebas.PV_smoketest   vs   Sonda.PV

sobre un intervalo de `date` (inclusivo).

Método (dos niveles)
--------------------
1. EXACTO, en BigQuery — fuente de verdad del veredicto.
   Comparación como MULTICONJUNTO por hash de fila, por (date, turno), con los
   mismos helpers que scripts/audit_viaje_parity.py. Las filas duplicadas que
   Sonda emite dentro del mismo reporte se cuentan bien: si una fila aparece
   2 veces en cada lado, coincide; si aparece 2 vs 1, sobra una.

2. DIAGNÓSTICO, en pandas — explica las discrepancias.
   Se descargan las filas del intervalo de ambas tablas (~3.4k por día), TODAS
   las columnas casteadas a STRING del lado de BigQuery (no hay coerción de
   tipos en pandas que fabrique diferencias falsas). Se quitan las coincidencias
   exactas (multiconjunto) y lo que sobra se empareja por la llave
   (date, turno, ruta, jornada):

     * DIFERENCIA DE VALOR: misma llave, 1 fila sobrante por lado, alguna
       columna distinta. Se reporta columna, valor prod y valor test. Aquí
       aparecen diferencias de formato (ej. economico "1234" vs "1234.0") o
       cambios reales en Sonda entre la hora del job legacy y la corrida local
       (PV es una foto del momento de la consulta).
     * PERTENENCIA: la llave sobra en un solo lado (solo_prod / solo_test).
     * AMBIGUA: la llave sobra en ambos lados pero repetida; no se puede
       emparejar fila a fila. Se vuelcan las filas para revisión manual.

   Los totales del diagnóstico se cruzan contra los del nivel 1; si no
   cuadran se avisa (dos caminos independientes al mismo número).

Uso
---
    python -m scripts.audit_pv_parity --start 2026-10-06
    python -m scripts.audit_pv_parity --start 2026-10-06 --end 2026-10-08 --out-dir .\\audit_pv

Precondición: pruebas.PV_smoketest contiene SOLO corridas del pipeline migrado
(TRUNCATE antes de la auditoría: el smoke test deja un día sintético).

Auth: ADC (bigquery.Client). Project por --project o env BQ_PROJECT.
Datasets por env BQ_DATASET_SONDA / BQ_DATASET_PRUEBAS.

Códigos de salida: 0 = idénticas, 1 = discrepancias a revisar, 2 = error/schema/sin datos.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
from google.cloud import bigquery

from config.settings import BQ_DATASET_PRUEBAS, BQ_DATASET_SONDA
from scripts.audit_viaje_parity import (
    _cols_proj,
    _row_hash_expr,
    compare_schemas,
    get_schema,
)

# Llave de emparejamiento para el diagnóstico (no es única: Sonda duplica filas)
KEY = ["date", "turno", "ruta", "jornada"]
NULL = "<NULL>"


# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------
def _date_params(start: date, end: date) -> list[bigquery.ScalarQueryParameter]:
    """[start 00:00, end+1 00:00) como DATETIME, idéntico en ambos lados."""
    return [
        bigquery.ScalarQueryParameter("start", "DATETIME", datetime(start.year, start.month, start.day)),
        bigquery.ScalarQueryParameter("end_excl", "DATETIME",
                                      datetime(end.year, end.month, end.day) + timedelta(days=1)),
    ]


_WHERE = "`date` >= @start AND `date` < @end_excl"


def run_summary(client: bigquery.Client, prod_fqn: str, test_fqn: str,
                cols: list[str], start: date, end: date) -> pd.DataFrame:
    """Nivel 1: multiconjunto exacto por (date, turno), calculado en BigQuery."""
    h = _row_hash_expr(cols)
    sql = f"""
    WITH
      prod AS (SELECT DATE(`date`) AS d, turno, {h} AS h FROM `{prod_fqn}` WHERE {_WHERE}),
      test AS (SELECT DATE(`date`) AS d, turno, {h} AS h FROM `{test_fqn}` WHERE {_WHERE}),
      p AS (SELECT d, turno, h, COUNT(*) AS c FROM prod GROUP BY d, turno, h),
      t AS (SELECT d, turno, h, COUNT(*) AS c FROM test GROUP BY d, turno, h),
      j AS (SELECT d, turno, IFNULL(p.c, 0) AS pc, IFNULL(t.c, 0) AS tc
            FROM p FULL OUTER JOIN t USING (d, turno, h))
    SELECT
      d AS fecha, turno,
      SUM(pc)                    AS prod_total,
      SUM(tc)                    AS test_total,
      SUM(LEAST(pc, tc))         AS identicas,
      SUM(GREATEST(pc - tc, 0))  AS solo_prod,
      SUM(GREATEST(tc - pc, 0))  AS solo_test
    FROM j
    GROUP BY fecha, turno
    ORDER BY fecha, turno
    """
    cfg = bigquery.QueryJobConfig(query_parameters=_date_params(start, end))
    return client.query(sql, job_config=cfg).result().to_dataframe()


def fetch_rows(client: bigquery.Client, fqn: str, schema: list[tuple[str, str]],
               start: date, end: date) -> pd.DataFrame:
    """Nivel 2: filas del intervalo con TODAS las columnas casteadas a STRING en BQ."""
    proj = ", ".join(
        f"`{name}`" if ftype == "STRING" else f"CAST(`{name}` AS STRING) AS `{name}`"
        for name, ftype in schema
    )
    sql = f"SELECT {proj} FROM `{fqn}` WHERE {_WHERE}"
    cfg = bigquery.QueryJobConfig(query_parameters=_date_params(start, end))
    df = client.query(sql, job_config=cfg).result().to_dataframe()
    return df.astype(object).where(df.notna(), NULL)


# --------------------------------------------------------------------------
# Diagnóstico en pandas
# --------------------------------------------------------------------------
def multiset_leftovers(prod: pd.DataFrame, test: pd.DataFrame,
                       cols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Quita coincidencias exactas como multiconjunto; devuelve lo que sobra de cada lado.

    cumcount numera las repeticiones de cada fila idéntica (0, 1, 2...), así el
    merge empareja la k-ésima copia de un lado con la k-ésima del otro.
    """
    p = prod.copy()
    t = test.copy()
    p["__n"] = p.groupby(cols, dropna=False).cumcount()
    t["__n"] = t.groupby(cols, dropna=False).cumcount()
    m = p.merge(t, on=cols + ["__n"], how="outer", indicator=True)
    only_p = m[m["_merge"] == "left_only"][cols].reset_index(drop=True)
    only_t = m[m["_merge"] == "right_only"][cols].reset_index(drop=True)
    return only_p, only_t


def classify(only_p: pd.DataFrame, only_t: pd.DataFrame,
             cols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Empareja sobrantes por KEY -> (diferencias de valor, filas sin pareja)."""
    value_cols = [c for c in cols if c not in KEY]
    gp = {k: g for k, g in only_p.groupby(KEY, dropna=False)} if len(only_p) else {}
    gt = {k: g for k, g in only_t.groupby(KEY, dropna=False)} if len(only_t) else {}

    valor_rows: list[dict] = []
    filas: list[pd.DataFrame] = []

    for k in sorted(set(gp) | set(gt), key=lambda x: tuple(map(str, x))):
        rp, rt = gp.get(k), gt.get(k)
        if rp is not None and rt is not None and len(rp) == 1 and len(rt) == 1:
            a, b = rp.iloc[0], rt.iloc[0]
            for c in value_cols:
                if a[c] != b[c]:
                    valor_rows.append({**dict(zip(KEY, k)), "columna": c,
                                       "valor_prod": a[c], "valor_test": b[c]})
            continue
        tipo = "ambigua" if (rp is not None and rt is not None) else None
        if rp is not None:
            filas.append(rp.assign(__lado="prod", __tipo=tipo or "solo_prod"))
        if rt is not None:
            filas.append(rt.assign(__lado="test", __tipo=tipo or "solo_test"))

    df_valor = pd.DataFrame(valor_rows, columns=KEY + ["columna", "valor_prod", "valor_test"])
    df_filas = (pd.concat(filas, ignore_index=True)[["__lado", "__tipo"] + cols]
                if filas else pd.DataFrame(columns=["__lado", "__tipo"] + cols))
    return df_valor, df_filas


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def _valid_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha inválida (usa YYYY-MM-DD): {s!r}")


def _write(df: pd.DataFrame, path: str, limit: int) -> str:
    df.head(limit).to_csv(path, index=False, encoding="utf-8")
    capped = f" (TOPE {limit}, hay {len(df)})" if len(df) > limit else ""
    return f"{min(len(df), limit)} filas -> {path}{capped}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Auditoría de paridad pipeline_PV vs job legacy.")
    ap.add_argument("--start", required=True, type=_valid_date,
                    help="Primer día YYYY-MM-DD (inclusivo).")
    ap.add_argument("--end", default=None, type=_valid_date,
                    help="Último día YYYY-MM-DD (inclusivo). Default: igual a --start.")
    ap.add_argument("--project", default=os.environ.get("BQ_PROJECT"),
                    help="GCP project (default: env BQ_PROJECT).")
    ap.add_argument("--out-dir", default="audit_pv",
                    help="Directorio para los CSV de resultados (default: ./audit_pv).")
    ap.add_argument("--dump-limit", type=int, default=5000,
                    help="Máx. filas por CSV de discrepancias (default 5000).")
    args = ap.parse_args()

    end = args.end or args.start
    if end < args.start:
        ap.error("--end no puede ser anterior a --start.")
    if not args.project:
        ap.error("Falta project: pásalo con --project o define BQ_PROJECT.")
    os.makedirs(args.out_dir, exist_ok=True)

    prod_fqn = f"{args.project}.{BQ_DATASET_SONDA}.PV"
    test_fqn = f"{args.project}.{BQ_DATASET_PRUEBAS}.PV_smoketest"
    client = bigquery.Client(project=args.project)

    print("=" * 76)
    print("  AUDITORÍA DE PARIDAD — pipeline_PV (test) vs job legacy (prod)")
    print("=" * 76)
    print(f"  prod = {prod_fqn}")
    print(f"  test = {test_fqn}")
    print(f"  intervalo = {args.start} .. {end} (inclusivo)\n")

    # 1. Schema
    try:
        ps, ts = get_schema(client, prod_fqn), get_schema(client, test_fqn)
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR obteniendo schema: {exc}")
        sys.exit(2)
    ok_schema, msgs = compare_schemas(ts, ps)  # (pipeline, legacy)
    for m in msgs:
        print(m)
    if not ok_schema:
        print("  VEREDICTO: SCHEMA MISMATCH — no se compara data.")
        sys.exit(2)
    cols_sorted = sorted(n for n, _ in ps)   # para el hash (orden por nombre)
    cols = [n for n, _ in ps]                # orden de la tabla, para los CSV

    # 2. Nivel 1: exacto en BigQuery
    summary = run_summary(client, prod_fqn, test_fqn, cols_sorted, args.start, end)
    if summary.empty:
        print("  Sin filas en ninguna de las dos tablas para el intervalo.")
        sys.exit(2)

    summary["estado"] = "IDÉNTICO"
    summary.loc[(summary.solo_prod + summary.solo_test) > 0, "estado"] = "DISCREPANCIAS"
    summary.loc[summary.test_total == 0, "estado"] = "SOLO EN PROD (no corrió el pipeline local)"
    summary.loc[summary.prod_total == 0, "estado"] = "SOLO EN TEST (no corrió el job legacy)"

    print("── Resumen exacto por día y turno (BigQuery) " + "─" * 31)
    print(summary.to_string(index=False))
    print()
    summary.to_csv(os.path.join(args.out_dir, "resumen_por_dia.csv"), index=False, encoding="utf-8")

    bq_only_prod = int(summary.solo_prod.sum())
    bq_only_test = int(summary.solo_test.sum())
    if bq_only_prod + bq_only_test == 0:
        print("  VEREDICTO: IDÉNTICAS ✓  (multiconjunto exacto en todo el intervalo)")
        print(f"  Resumen -> {os.path.join(args.out_dir, 'resumen_por_dia.csv')}")
        sys.exit(0)

    # 3. Nivel 2: diagnóstico en pandas
    prod = fetch_rows(client, prod_fqn, ps, args.start, end)
    test = fetch_rows(client, test_fqn, ts, args.start, end)[cols]
    only_p, only_t = multiset_leftovers(prod[cols], test, cols)

    if (len(only_p), len(only_t)) != (bq_only_prod, bq_only_test):
        print(f"  (aviso) el diagnóstico no cuadra con BigQuery: pandas solo_prod={len(only_p)} "
              f"solo_test={len(only_t)} vs BQ {bq_only_prod}/{bq_only_test}. "
              f"El veredicto usa BigQuery; revisar el diagnóstico con cautela.")

    df_valor, df_filas = classify(only_p, only_t, cols)

    print("── Diagnóstico (pandas) " + "─" * 53)
    n_pares = df_valor[KEY].drop_duplicates().shape[0] if len(df_valor) else 0
    print(f"  Jornadas con DIFERENCIA DE VALOR: {n_pares}")
    if len(df_valor):
        por_col = df_valor["columna"].value_counts()
        for c, n in por_col.items():
            ejemplo = df_valor[df_valor.columna == c].iloc[0]
            print(f"    {c:<20} {n:>6}   ej. prod={ejemplo.valor_prod!r} test={ejemplo.valor_test!r}")
    if len(df_filas):
        for tipo, n in df_filas["__tipo"].value_counts().items():
            print(f"  Filas {tipo.upper():<10}: {n}")
    print()
    print("  " + _write(df_valor, os.path.join(args.out_dir, "discrepancias_valor.csv"), args.dump_limit))
    print("  " + _write(df_filas, os.path.join(args.out_dir, "discrepancias_filas.csv"), args.dump_limit))

    print("=" * 76)
    print("  VEREDICTO: DISCREPANCIAS — revisar si son de formato/horario (valor) o")
    print("  filas faltantes/sobrantes (pertenencia). exit code = 1")
    sys.exit(1)


if __name__ == "__main__":
    main()
