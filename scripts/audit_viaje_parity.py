#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scripts/audit_viaje_parity.py

Auditoría de paridad para pipeline_Viaje.

Compara las tres tablas que alimenta el pipeline EN CONSTRUCCIÓN (dataset
`pruebas`, sufijo _smoketest) contra las tablas históricas que alimentan los
notebooks LEGACY (producción):

    pruebas.VIAJES_smoketest                      vs  Sonda.VIAJES
    pruebas.INTERVALOS_smoketest                  vs  Sonda.INTERVALOS
    pruebas.INTERVALOS_Y_CUMPLIMIENTOS_smoketest  vs  TIEMPO_INTERTRAMOS.INTERVALOS_Y_CUMPLIMIENTOS

Método
------
Comparación como MULTICONJUNTO por hash de fila, ejecutada del lado de BigQuery
(NO se descargan las tablas completas). Razones:

  * Bajar a pandas coerciona tipos (INT64 con NULL -> float, TIME/DATETIME
    reformateados) y fabrica discrepancias falsas; comparar donde viven los
    datos preserva la bit-exactitud pedida.
  * `TO_JSON_STRING` serializa NULL como `null` de forma consistente, así que
    los NULLs comparan bien (un JOIN por columnas con `=` los perdería).
  * Las columnas se ordenan por NOMBRE antes de hashear, de modo que un ORDEN
    de columnas distinto entre ambas tablas no produzca una diferencia falsa
    (sí se reporta como aviso).

Criterio de lectura
-------------------
  * IDÉNTICAS  -> multiconjunto igual en ambos lados para el rango [cutoff, until).
  * Discrepancia de MEMBRESÍA (fila entera en un solo lado): esperada y
    aceptable — es el no-determinismo del IsolationForest marcando outliers
    distintos entre corridas.
  * Discrepancia de VALOR (misma viaje, valores distintos): PROHIBIDA — es un
    bug de lógica. En el CSV de volcado aparece como dos filas casi idénticas
    adyacentes (por eso se ordena por todas las columnas).

El schema se INTROSPECTA en runtime (no se hardcodea); INTERVALOS no tiene
schema explícito en el repo porque lo genera el SQL runner, y aun así se compara.

Uso
---
    python -m scripts.audit_viaje_parity --cutoff 2026-07-13
    python -m scripts.audit_viaje_parity --cutoff 2026-07-13 --until 2026-07-21
    python -m scripts.audit_viaje_parity --cutoff 2026-07-13 --out-dir ./audit_out

Supuesto de rango: el script filtra FECHA >= cutoff (y opcionalmente < until)
IDÉNTICO en ambos lados. Que ambas tablas estén pobladas sobre el mismo rango
es responsabilidad del operador; si un lado tiene semanas de más, saldrán como
discrepancias de un solo lado.

Auth: ADC (google.auth.default vía bigquery.Client). En local,
GOOGLE_APPLICATION_CREDENTIALS -> SA key. Project por --project o env BQ_PROJECT
(nunca hardcodeado: el repo es público). Datasets por env
BQ_DATASET_SONDA / BQ_DATASET_INTERTRAMOS / BQ_DATASET_PRUEBAS.

Códigos de salida: 0 = idénticas, 1 = discrepancias a revisar, 2 = error/schema.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime

from google.cloud import bigquery
from dotenv import load_dotenv
load_dotenv()


# --------------------------------------------------------------------------
# Configuración de pares a comparar
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParityPair:
    name: str
    pipeline_fqn: str
    legacy_fqn: str


def build_pairs(project: str) -> list[ParityPair]:
    """FQNs compuestos desde el project (obligatorio no-literal) y datasets por
    env, alineados con el contrato de env vars del Job."""
    ds_sonda = os.environ.get("BQ_DATASET_SONDA", "Sonda")
    ds_inter = os.environ.get("BQ_DATASET_INTERTRAMOS", "TIEMPO_INTERTRAMOS")
    ds_prueb = os.environ.get("BQ_DATASET_PRUEBAS", "pruebas")
    return [
        ParityPair(
            "VIAJES",
            f"{project}.{ds_prueb}.VIAJES_smoketest",
            f"{project}.{ds_sonda}.VIAJES",
        ),
        ParityPair(
            "INTERVALOS",
            f"{project}.{ds_prueb}.INTERVALOS_smoketest",
            f"{project}.{ds_sonda}.INTERVALOS",
        ),
        ParityPair(
            "INTERVALOS_Y_CUMPLIMIENTOS",
            f"{project}.{ds_prueb}.INTERVALOS_Y_CUMPLIMIENTOS_smoketest",
            f"{project}.{ds_inter}.INTERVALOS_Y_CUMPLIMIENTOS",
        ),
    ]


# --------------------------------------------------------------------------
# Introspección y comparación de schema
# --------------------------------------------------------------------------
def get_schema(client: bigquery.Client, fqn: str) -> list[tuple[str, str]]:
    tbl = client.get_table(fqn)
    return [(f.name, f.field_type) for f in tbl.schema]


def compare_schemas(pipe: list[tuple[str, str]],
                    leg: list[tuple[str, str]]) -> tuple[bool, list[str]]:
    """(ok, mensajes). Compara conjuntos de (nombre, tipo). Diferencia de ORDEN
    es solo aviso (no rompe el hash, que ordena por nombre)."""
    pipe_map, leg_map = dict(pipe), dict(leg)
    msgs: list[str] = []
    ok = True

    only_pipe = set(pipe_map) - set(leg_map)
    only_leg = set(leg_map) - set(pipe_map)
    if only_pipe:
        ok = False
        msgs.append(f"  Columnas solo en pipeline: {sorted(only_pipe)}")
    if only_leg:
        ok = False
        msgs.append(f"  Columnas solo en legacy:   {sorted(only_leg)}")
    for col in sorted(set(pipe_map) & set(leg_map)):
        if pipe_map[col] != leg_map[col]:
            ok = False
            msgs.append(
                f"  Tipo distinto en '{col}': pipeline={pipe_map[col]} "
                f"vs legacy={leg_map[col]}"
            )
    if ok and [n for n, _ in pipe] != [n for n, _ in leg]:
        msgs.append("  (aviso) el ORDEN de columnas difiere; no afecta el hash "
                    "(se ordena por nombre), pero conviene alinearlo.")
    return ok, msgs


def sorted_cols(schema: list[tuple[str, str]]) -> list[str]:
    return sorted(name for name, _ in schema)


def fecha_type(schema: list[tuple[str, str]]) -> str | None:
    for name, ftype in schema:
        if name.upper() == "FECHA":
            return ftype
    return None


# --------------------------------------------------------------------------
# Construcción de SQL
# --------------------------------------------------------------------------
def _row_hash_expr(cols: list[str]) -> str:
    inner = ", ".join(f"`{c}`" for c in cols)
    return f"TO_HEX(MD5(TO_JSON_STRING(STRUCT({inner}))))"


def _cols_proj(cols: list[str]) -> str:
    return ", ".join(f"`{c}`" for c in cols)


def _where_clause(ftype: str | None, has_until: bool) -> str:
    if ftype is None:
        return "TRUE"
    clause = "FECHA >= @cutoff"
    if has_until:
        clause += " AND FECHA < @until"
    return clause


def _date_params(ftype: str | None, cutoff: str,
                 until: str | None) -> list[bigquery.ScalarQueryParameter]:
    """Castea el cutoff/until al tipo de FECHA. Se aplica IDÉNTICO a ambos lados,
    así que un desfase de zona horaria en TIMESTAMP no puede introducir una
    discrepancia falsa (incluye/excluye las mismas filas en ambas tablas)."""
    if ftype is None:
        return []
    t = ftype.upper()

    def mk(name: str, value: str) -> bigquery.ScalarQueryParameter:
        if t == "DATE":
            return bigquery.ScalarQueryParameter(name, "DATE", value)
        if t == "DATETIME":
            return bigquery.ScalarQueryParameter(name, "DATETIME", f"{value}T00:00:00")
        if t == "TIMESTAMP":
            return bigquery.ScalarQueryParameter(name, "TIMESTAMP", f"{value}T00:00:00")
        raise ValueError(f"Tipo de FECHA no soportado para cutoff: {ftype}")

    params = [mk("cutoff", cutoff)]
    if until is not None:
        params.append(mk("until", until))
    return params


def run_summary(client, pair, cols, ftype, cutoff, until):
    h = _row_hash_expr(cols)
    where = _where_clause(ftype, until is not None)
    sql = f"""
    WITH
      pipeline AS (SELECT {h} AS h FROM `{pair.pipeline_fqn}` WHERE {where}),
      legacy   AS (SELECT {h} AS h FROM `{pair.legacy_fqn}`   WHERE {where}),
      p AS (SELECT h, COUNT(*) AS c FROM pipeline GROUP BY h),
      l AS (SELECT h, COUNT(*) AS c FROM legacy   GROUP BY h),
      j AS (SELECT IFNULL(p.c, 0) AS pc, IFNULL(l.c, 0) AS lc
            FROM p FULL OUTER JOIN l USING (h))
    SELECT
      IFNULL(SUM(LEAST(pc, lc)), 0)          AS matched_rows,
      IFNULL(SUM(GREATEST(pc - lc, 0)), 0)   AS only_pipeline_rows,
      IFNULL(SUM(GREATEST(lc - pc, 0)), 0)   AS only_legacy_rows,
      (SELECT COUNT(*) FROM pipeline)        AS pipeline_total,
      (SELECT COUNT(*) FROM legacy)          AS legacy_total
    FROM j
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=_date_params(ftype, cutoff, until))
    return list(client.query(sql, job_config=cfg).result())[0]


def run_dump(client, pair, cols, ftype, cutoff, until, limit):
    h = _row_hash_expr(cols)
    proj = _cols_proj(cols)
    where = _where_clause(ftype, until is not None)
    sql = f"""
    WITH
      pipeline AS (SELECT {proj}, {h} AS __h FROM `{pair.pipeline_fqn}` WHERE {where}),
      legacy   AS (SELECT {proj}, {h} AS __h FROM `{pair.legacy_fqn}`   WHERE {where}),
      p AS (SELECT __h, COUNT(*) AS c FROM pipeline GROUP BY __h),
      l AS (SELECT __h, COUNT(*) AS c FROM legacy   GROUP BY __h),
      diff AS (SELECT __h FROM p FULL OUTER JOIN l USING (__h)
               WHERE IFNULL(p.c, 0) != IFNULL(l.c, 0))
    SELECT '1_pipeline' AS __side, {proj}
      FROM pipeline WHERE __h IN (SELECT __h FROM diff)
    UNION ALL
    SELECT '2_legacy' AS __side, {proj}
      FROM legacy   WHERE __h IN (SELECT __h FROM diff)
    ORDER BY {proj}, __side
    LIMIT {int(limit)}
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=_date_params(ftype, cutoff, until))
    return client.query(sql, job_config=cfg).result()


def dump_to_csv(rows, cols: list[str], path: str) -> int:
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["__side"] + cols)
        for r in rows:
            w.writerow([r["__side"]] + [r[c] for c in cols])
            n += 1
    return n


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def _valid_date(s: str) -> str:
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha inválida (usa YYYY-MM-DD): {s!r}")
    return s


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Auditoría de paridad pipeline_Viaje vs legacy.")
    ap.add_argument("--cutoff", required=True, type=_valid_date,
                    help="Fecha de corte YYYY-MM-DD; compara FECHA >= cutoff en ambos lados.")
    ap.add_argument("--until", default=None, type=_valid_date,
                    help="Opcional YYYY-MM-DD; acota superiormente con FECHA < until.")
    ap.add_argument("--project", default=os.environ.get("BQ_PROJECT"),
                    help="GCP project (default: env BQ_PROJECT).")
    ap.add_argument("--out-dir", default=".",
                    help="Directorio para los CSV de discrepancias (default: cwd).")
    ap.add_argument("--dump-limit", type=int, default=5000,
                    help="Máx. filas discrepantes a volcar por tabla (default 5000).")
    ap.add_argument("--no-dump", action="store_true",
                    help="Solo resumen, sin volcar filas discrepantes.")
    args = ap.parse_args()

    if not args.project:
        ap.error("Falta project: pásalo con --project o define BQ_PROJECT.")
    os.makedirs(args.out_dir, exist_ok=True)

    client = bigquery.Client(project=args.project)
    pairs = build_pairs(args.project)

    exit_code = 0
    for pair in pairs:
        print("=" * 76)
        print(f"[{pair.name}]")
        print(f"  pipeline = {pair.pipeline_fqn}")
        print(f"  legacy   = {pair.legacy_fqn}")

        try:
            ps = get_schema(client, pair.pipeline_fqn)
            ls = get_schema(client, pair.legacy_fqn)
        except Exception as exc:  # noqa: BLE001 — reportar y seguir con las demás
            print(f"  ERROR obteniendo schema: {exc}")
            exit_code = max(exit_code, 2)
            continue

        ok, msgs = compare_schemas(ps, ls)
        for m in msgs:
            print(m)
        if not ok:
            print("  VEREDICTO: SCHEMA MISMATCH — no se compara data.")
            exit_code = max(exit_code, 2)
            continue

        cols = sorted_cols(ps)
        ftype = fecha_type(ps)
        if ftype is None:
            print("  (aviso) sin columna FECHA; se compara la tabla completa sin cutoff.")

        s = run_summary(client, pair, cols, ftype, args.cutoff, args.until)
        diffs = int(s.only_pipeline_rows) + int(s.only_legacy_rows)
        print(f"  filas: pipeline_total={s.pipeline_total}  legacy_total={s.legacy_total}")
        print(f"  idénticas (multiconjunto): {s.matched_rows}")
        print(f"  solo en pipeline: {s.only_pipeline_rows}   "
              f"solo en legacy: {s.only_legacy_rows}")

        if diffs == 0:
            print("  VEREDICTO: IDÉNTICAS ✓")
            continue

        print("  VEREDICTO: DISCREPANCIAS — revisar si es ruido de IsolationForest "
              "(filas sueltas) o divergencia de valores (pares casi idénticos).")
        exit_code = max(exit_code, 1)
        if not args.no_dump:
            out_path = os.path.join(args.out_dir, f"discrepancias_{pair.name}.csv")
            written = dump_to_csv(
                run_dump(client, pair, cols, ftype, args.cutoff, args.until, args.dump_limit),
                cols, out_path)
            capped = " (TOPE alcanzado, hay más)" if written >= args.dump_limit else ""
            print(f"  volcadas {written} filas discrepantes -> {out_path}{capped}")

    print("=" * 76)
    print(f"exit code = {exit_code}  (0=idéntico, 1=discrepancias a revisar, 2=error/schema)")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()