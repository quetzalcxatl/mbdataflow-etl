#!/usr/bin/env python
# scripts/smoketest_bq_sql_runner.py
# -*- coding: utf-8 -*-
"""
Smoke test del BigQuerySQLRunner ejecutando la query INTERVALOSDINAMICOS
contra tablas DUMMY (nunca producción).

Valida cinco cosas antes de apuntar el runner a `Sonda.VIAJES` real:

  1. Rechazo de FQN malformado         — la defensa contra inyección al construir.
  2. Dry-run detecta fuente inexistente — falla en el segundo 0, no tras el CREATE.
  3. Primera creación                  — el CREATE OR REPLACE funciona sobre
                                          tabla destino inexistente, y sale con
                                          filas > 0.
  4. Schema del destino == producción  — Sonda.INTERVALOS y el destino dummy
                                          coinciden en nombre + tipo + orden.
                                          CRÍTICO: si divergen, en producción
                                          el CREATE OR REPLACE mutaría el
                                          schema real y rompería Looker.
  5. Idempotencia                      — segunda corrida reemplaza con mismo
                                          conteo. Verifica que la guarda de
                                          regresión (min_row_ratio=0.9) no
                                          dispare cuando no debe.

QUÉ NO VALIDA (deliberado):
  - Que la guarda dispare bajo condición adversa (mutilar VIAJES para forzar
    la regresión). Fabrizio pidió no ejercerlo — el estado de la fuente se
    mantiene consistente para el próximo uso del smoke test del loader.
  - Corrección de negocio de INTERVALOSDINAMICOS — eso es paridad contra el
    proceso manual, roadmap aparte.

PRECONDICIONES:
  - VIAJES_smoketest cargada con datos (típicamente tras correr el smoke
    test del loader). El check 3 no puede correr sobre tabla vacía.
  - INTERVALOS_smoketest NO DEBE EXISTIR — el script la elimina al inicio.
  - La SA tiene lectura en `Sonda.INTERVALOS` (para leer su schema).
  - pip install db-dtypes  (para to_dataframe en los queries de verificación).

Uso:
    python -m scripts.smoketest_bq_sql_runner
"""

from __future__ import annotations

import sys
from pathlib import Path

import google.auth
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from transform.bq_sql_runner import BigQuerySQLRunner


# ==========================================================================
# CONFIG
# ==========================================================================
PROJECT = "centrodecontrol"
DATASET_PRUEBAS = "pruebas"
DATASET_SONDA = "Sonda"

# Fuente: tabla ya cargada por el smoke test del loader (2 semanas dentro).
SOURCE_DUMMY = f"{PROJECT}.{DATASET_PRUEBAS}.VIAJES_smoketest"

# Destino: la crea la query. NO debe existir al inicio.
DEST_DUMMY = f"{PROJECT}.{DATASET_PRUEBAS}.INTERVALOS_smoketest"

# Referencia de schema esperado.
PROD_INTERVALOS = f"{PROJECT}.{DATASET_SONDA}.INTERVALOS"

# El .sql versionado que ejecuta el runner.
SQL_PATH = Path("transform/sql/intervalos_dinamicos.sql")


# ==========================================================================
# Utilidades
# ==========================================================================
def _client() -> bigquery.Client:
    creds, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(credentials=creds, project=project)


def _drop_if_exists(client: bigquery.Client, table_id: str) -> None:
    client.query(f"DROP TABLE IF EXISTS `{table_id}`").result()


def _table_exists(client: bigquery.Client, table_id: str) -> bool:
    try:
        client.get_table(table_id)
        return True
    except NotFound:
        return False


def _count(client: bigquery.Client, table_id: str) -> int:
    rows = client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`").result()
    return list(rows)[0]["n"]


def _read_schema(client: bigquery.Client, table_id: str) -> list[tuple[str, str]]:
    """
    Lee (column_name, data_type) en ORDEN de INFORMATION_SCHEMA de la tabla dada.

    El orden importa: el `CREATE OR REPLACE` de la query define el orden por el
    orden del SELECT, y consumidores posicionales (algunos loaders, algunos
    UNION históricos) dependen de eso.
    """
    project, dataset, table = table_id.split(".")
    q = f"""
        SELECT column_name, data_type
        FROM `{project}.{dataset}`.INFORMATION_SCHEMA.COLUMNS
        WHERE table_name = @t
        ORDER BY ordinal_position
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("t", "STRING", table)]
    )
    rows = client.query(q, job_config=cfg).result()
    return [(r["column_name"], r["data_type"]) for r in rows]


class Reporter:
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
def check_reject_malformed_fqn(rep: Reporter) -> None:
    """
    Check 1: FQN malformado se rechaza al CONSTRUIR el runner, sin tocar BQ.

    Ejerce la regex de validación con vectores clásicos de inyección. Es rápido
    porque no llega a la red; falla el constructor con ValueError.
    """
    vectores = [
        "p.d.t;DROP TABLE x",       # separador de statements
        "p.d.t`; DROP TABLE x; --", # cierre de backtick + comment
        "p.d.tabla con espacio",    # espacios
        "solo.dos",                 # falta parte
        "",                         # vacío
    ]

    todos_rechazados = True
    detalles = []
    for v in vectores:
        try:
            BigQuerySQLRunner(SQL_PATH, source_table=v, dest_table=DEST_DUMMY)
            todos_rechazados = False
            detalles.append(f"aceptó {v!r}  ← BUG")
        except ValueError:
            pass  # esperado
        except Exception as e:
            todos_rechazados = False
            detalles.append(f"{v!r} lanzó {type(e).__name__} en vez de ValueError")

    detail = "5/5 vectores rechazados" if todos_rechazados else "; ".join(detalles)
    rep.record("SQL Runner: rechaza FQN malformado", todos_rechazados, detail)


def check_dry_run_missing_source(client: bigquery.Client, rep: Reporter) -> None:
    """
    Check 2: si la tabla fuente no existe, el dry-run debe fallar ANTES del
    CREATE OR REPLACE.

    En producción esto ahorra el escenario donde alguien apunta el runner a
    una tabla equivocada y descubre el error después de reemplazar el destino.
    """
    fuente_inexistente = f"{PROJECT}.{DATASET_PRUEBAS}.NO_EXISTE_smoketest"
    _drop_if_exists(client, fuente_inexistente)

    # Guardar estado del destino antes de ejecutar (para verificar no-toque)
    dest_existia = _table_exists(client, DEST_DUMMY)
    dest_conteo_antes = _count(client, DEST_DUMMY) if dest_existia else None

    try:
        runner = BigQuerySQLRunner(
            SQL_PATH, source_table=fuente_inexistente, dest_table=DEST_DUMMY,
            min_row_ratio=0.0,
        )
        runner.run()
        rep.record(
            "SQL Runner: dry-run detecta fuente inexistente", False,
            "run() no lanzó — el CREATE OR REPLACE se ejecutó con fuente inválida",
        )
    except Exception as e:
        # Cualquier excepción es aceptable siempre que no haya tocado el destino
        dest_existe_despues = _table_exists(client, DEST_DUMMY)
        conteo_despues = _count(client, DEST_DUMMY) if dest_existe_despues else None
        no_toco = (dest_existia == dest_existe_despues and
                   dest_conteo_antes == conteo_despues)
        rep.record(
            "SQL Runner: dry-run detecta fuente inexistente", no_toco,
            f"abortó con {type(e).__name__}"
            + ("" if no_toco else "  ← PERO tocó el destino"),
        )


def check_first_creation(client: bigquery.Client, rep: Reporter) -> int:
    """
    Check 3: sobre destino inexistente, el runner crea la tabla y sale con
    filas > 0. Con min_row_ratio=0.0 porque no hay "antes" contra qué comparar.

    Retorna el conteo del destino para que checks siguientes lo reusen.
    """
    _drop_if_exists(client, DEST_DUMMY)
    assert not _table_exists(client, DEST_DUMMY), "precondición: destino no existe"

    try:
        runner = BigQuerySQLRunner(
            SQL_PATH, source_table=SOURCE_DUMMY, dest_table=DEST_DUMMY,
            min_row_ratio=0.0,
        )
        filas = runner.run()
    except Exception as e:
        rep.record(
            "SQL Runner: primera creación", False,
            f"{type(e).__name__}: {e}",
        )
        return 0

    creado = _table_exists(client, DEST_DUMMY)
    conteo_real = _count(client, DEST_DUMMY) if creado else 0
    passed = creado and conteo_real == filas > 0
    rep.record(
        "SQL Runner: primera creación", passed,
        f"tabla creada, filas={conteo_real}",
    )
    return conteo_real


def check_schema_matches_prod(client: bigquery.Client, rep: Reporter) -> None:
    """
    Check 4 (CRÍTICO): el schema de la tabla creada por la query debe coincidir
    EXACTAMENTE con Sonda.INTERVALOS en producción.

    Si divergen, en producción el CREATE OR REPLACE cambiaría el schema de la
    tabla real y rompería el campo calculado del dashboard que depende de
    INTERVALO_SEC.
    """
    try:
        prod = _read_schema(client, PROD_INTERVALOS)
    except Exception as e:
        rep.record(
            "SQL Runner: schema == producción", False,
            f"no se pudo leer schema de {PROD_INTERVALOS}: {type(e).__name__}",
        )
        return

    try:
        dummy = _read_schema(client, DEST_DUMMY)
    except Exception as e:
        rep.record(
            "SQL Runner: schema == producción", False,
            f"no se pudo leer schema del destino: {type(e).__name__}",
        )
        return

    if prod == dummy:
        rep.record(
            "SQL Runner: schema == producción", True,
            f"{len(prod)} columnas coinciden en nombre, tipo y orden",
        )
        return

    # Reporte útil cuando difieren, para que Fabrizio vea DÓNDE
    diffs = []
    for i, (p, d) in enumerate(zip(prod, dummy)):
        if p != d:
            diffs.append(f"pos {i}: prod={p} vs dummy={d}")
    if len(prod) != len(dummy):
        diffs.append(f"longitud: prod={len(prod)} vs dummy={len(dummy)}")
    rep.record(
        "SQL Runner: schema == producción", False,
        "; ".join(diffs) or "difieren en orden",
    )


def check_idempotency(client: bigquery.Client, rep: Reporter, conteo_previo: int) -> None:
    """
    Check 5: segunda corrida sobre destino existente con min_row_ratio=0.9.

    Verifica:
      - El CREATE OR REPLACE no falla sobre destino existente.
      - El conteo permanece igual (fuente no cambió).
      - La guarda de regresión NO dispara falsamente cuando la tabla no encoge.
    """
    if conteo_previo == 0:
        rep.record(
            "SQL Runner: idempotencia", False,
            "no se puede probar sin conteo previo del check 3",
        )
        return

    try:
        runner = BigQuerySQLRunner(
            SQL_PATH, source_table=SOURCE_DUMMY, dest_table=DEST_DUMMY,
            min_row_ratio=0.9,   # esta vez SÍ armada, la fuente no cambió
        )
        filas = runner.run()
    except Exception as e:
        rep.record(
            "SQL Runner: idempotencia", False,
            f"2da corrida lanzó {type(e).__name__}: {e}",
        )
        return

    passed = filas == conteo_previo
    rep.record(
        "SQL Runner: idempotencia", passed,
        f"antes={conteo_previo}, después={filas}"
        + ("" if passed else "  ← el conteo cambió sin razón"),
    )


# ==========================================================================
# Main
# ==========================================================================
def main() -> int:
    client = _client()

    print("\n" + "=" * 60)
    print("  SMOKE TEST — BigQuerySQLRunner + INTERVALOSDINAMICOS")
    print("=" * 60)
    print(f"  SQL:      {SQL_PATH}")
    print(f"  Source:   {SOURCE_DUMMY}")
    print(f"  Dest:     {DEST_DUMMY}")
    print(f"  Ref:      {PROD_INTERVALOS} (para schema)\n")

    # Precondición: fuente debe tener datos (del smoke test del loader)
    if not _table_exists(client, SOURCE_DUMMY) or _count(client, SOURCE_DUMMY) == 0:
        print(f"  ✗ ABORTAR: {SOURCE_DUMMY} no existe o está vacía.")
        print("    Correr antes: python -m scripts.smoketest_bigquery_loader\n")
        return 2

    rep = Reporter()

    check_reject_malformed_fqn(rep)                    # 1 — no toca BQ
    check_dry_run_missing_source(client, rep)          # 2 — no toca destino
    conteo = check_first_creation(client, rep)         # 3 — crea destino
    check_schema_matches_prod(client, rep)             # 4 — compara vs Sonda
    check_idempotency(client, rep, conteo)             # 5 — reemplaza destino

    all_passed = rep.summary()

    print(f"\n  {DEST_DUMMY} queda creada tras el test.")
    print(f"  Para limpiar: DROP TABLE `{DEST_DUMMY}`;\n")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())