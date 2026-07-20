# transform/bq_sql_runner.py
# -*- coding: utf-8 -*-
"""
Ejecutor de transformaciones SQL dentro de BigQuery (capa ELT post-carga).

Lee un .sql versionado del repo, sustituye los nombres de tabla y lo ejecuta.
Genérico: no sabe nada de Viaje. Cualquier pipeline que necesite una
transformación en el warehouse lo reutiliza.

POR QUÉ EL SQL VIVE EN UN ARCHIVO Y NO EN UN STRING DE PYTHON:
  - Se prueba tal cual en la consola de BigQuery, sin ejecutar Python.
  - `git diff` sobre SQL es legible; sobre un string multilínea no.
  - Lo lee cualquiera del equipo sin saber Python.

POR QUÉ SUSTITUCIÓN DE TEXTO Y NO QUERY PARAMETERS:
  BigQuery NO admite @parametros para IDENTIFICADORES (nombres de tabla),
  solo para valores. Para que el mismo .sql sirva en producción y contra las
  tablas dummy del smoke test, los FQN entran por sustitución.
  Eso abre la puerta a inyección, así que TODO identificador se valida contra
  una regex estricta antes de tocar el SQL. Los valores vienen de settings
  (env vars), nunca de entrada de usuario, pero la validación se hace igual:
  defensa en profundidad, y el repo es público.

GUARDAS (§5.6 — fallar ruidosamente, nunca en silencio):
  1. Dry run previo: valida sintaxis y reporta bytes a escanear ANTES de gastar.
  2. Conteo antes/después: si el destino queda en 0 filas, RuntimeError.
  3. Guarda de regresión: si el destino encoge por debajo de un umbral respecto
     de su conteo previo, RuntimeError. Una tabla que se reconstruye desde todo
     el histórico solo debería crecer; que encoja significa que la fuente está
     incompleta o corrupta, y un CREATE OR REPLACE la habría reemplazado por
     una versión mutilada sin avisar.

Ver Architecture.md §5.10 (Carga y transformación en BigQuery).
"""

from __future__ import annotations

import re
from pathlib import Path

import google.auth
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from utils.logger import ok, info, err


# project.dataset.table — el project ID admite guiones; dataset y tabla no.
# Cualquier cosa fuera de esto (backticks, espacios, ';', comentarios) se
# rechaza antes de entrar al SQL.
_TABLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


class BigQuerySQLRunner:
    """
    Ejecuta un .sql del repo contra BigQuery, sustituyendo tablas y validando.

    Args:
        sql_path:      Ruta al .sql versionado (ej. transform/sql/x.sql).
        source_table:  FQN 'project.dataset.tabla' que reemplaza {source_table}.
        dest_table:    FQN 'project.dataset.tabla' que reemplaza {dest_table}.
        min_row_ratio: El destino debe quedar con al menos esta fracción de las
                       filas que tenía antes. 0.9 tolera un 10% de merma por
                       variación real de datos; por debajo, aborta. Poner en 0.0
                       lo desactiva (útil solo para pruebas sobre tablas nuevas).
    """

    def __init__(
        self,
        sql_path: Path,
        source_table: str,
        dest_table: str,
        min_row_ratio: float = 0.9,
    ):
        self.sql_path = Path(sql_path)
        self.source_table = self._validate_table_id(source_table, "source_table")
        self.dest_table = self._validate_table_id(dest_table, "dest_table")
        self.min_row_ratio = min_row_ratio

    # ------------------------------------------------------------------
    # Validación y renderizado
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_table_id(table_id: str, label: str) -> str:
        """
        Acepta solo 'project.dataset.tabla'. Rechaza todo lo demás.

        Es la defensa contra inyección: como el identificador se interpola en el
        SQL como texto, un valor con backticks, ';' o '--' podría inyectar
        sentencias. La regex no deja pasar ninguno de esos caracteres.
        """
        if not isinstance(table_id, str) or not _TABLE_ID_RE.match(table_id):
            raise ValueError(
                f"SQL Runner: {label}='{table_id}' no tiene formato "
                f"'project.dataset.tabla' o contiene caracteres no permitidos."
            )
        return table_id

    def _render_sql(self) -> str:
        """Lee el .sql y sustituye los placeholders por los FQN validados."""
        if not self.sql_path.exists():
            raise FileNotFoundError(f"SQL Runner: no existe el archivo: {self.sql_path}")

        raw = self.sql_path.read_text(encoding="utf-8")

        faltantes = [
            ph for ph in ("{source_table}", "{dest_table}") if ph not in raw
        ]
        if faltantes:
            raise RuntimeError(
                f"SQL Runner: {self.sql_path.name} no contiene {faltantes}. "
                f"El archivo debe usar placeholders, no FQN hardcodeados."
            )

        return raw.replace("{source_table}", self.source_table).replace(
            "{dest_table}", self.dest_table
        )

    # ------------------------------------------------------------------
    # Cliente y utilidades
    # ------------------------------------------------------------------

    def _get_client(self) -> bigquery.Client:
        creds, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/bigquery"]
        )
        return bigquery.Client(credentials=creds, project=project)

    def _count_rows(self, client: bigquery.Client, table_id: str) -> int | None:
        """Conteo de la tabla, o None si aún no existe (primera corrida)."""
        try:
            rows = client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`").result()
            return list(rows)[0]["n"]
        except NotFound:
            return None

    def _dry_run(self, client: bigquery.Client, sql: str) -> int:
        """
        Valida la query sin ejecutarla. Retorna bytes que escanearía.

        Barato (no cuesta) y atrapa errores de sintaxis o de tabla inexistente
        ANTES de reemplazar nada en el destino.
        """
        cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = client.query(sql, job_config=cfg)
        return job.total_bytes_processed or 0

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self) -> int:
        """
        Ejecuta la transformación. Retorna las filas del destino resultante.

        Propaga toda excepción (§5.6): un fallo silencioso deja exit code 0,
        Cloud Run lo reporta SUCCESS y el dashboard se queda con datos viejos
        sin que ninguna alerta dispare.
        """
        sql = self._render_sql()
        client = self._get_client()

        # 1. Dry run: sintaxis + costo, antes de tocar el destino.
        bytes_scan = self._dry_run(client, sql)
        info(
            f"Dry run OK — {self.sql_path.name} escanearía "
            f"{bytes_scan / 1024**3:.2f} GiB de {self.source_table}"
        )

        # 2. Estado previo del destino (None si no existe todavía).
        antes = self._count_rows(client, self.dest_table)

        # 3. Ejecutar. CREATE OR REPLACE es atómico: si falla, el destino
        #    conserva su contenido anterior.
        job = client.query(sql)
        job.result()

        # 4. Verificar el resultado.
        despues = self._count_rows(client, self.dest_table)
        if despues is None:
            raise RuntimeError(
                f"SQL Runner: {self.dest_table} no existe después de ejecutar "
                f"{self.sql_path.name}. La query no creó la tabla destino."
            )
        if despues == 0:
            raise RuntimeError(
                f"SQL Runner: {self.dest_table} quedó en 0 filas tras "
                f"{self.sql_path.name}. Fuente vacía, incompleta o schema drift."
            )

        # 5. Guarda de regresión: una tabla reconstruida desde todo el histórico
        #    solo debería crecer. Que encoja apunta a fuente mutilada.
        if antes is not None and self.min_row_ratio > 0:
            minimo = int(antes * self.min_row_ratio)
            if despues < minimo:
                raise RuntimeError(
                    f"SQL Runner: {self.dest_table} ENCOGIÓ de {antes} a {despues} "
                    f"filas (mínimo tolerado {minimo}, ratio {self.min_row_ratio}). "
                    f"Revisar {self.source_table} antes de confiar en el resultado."
                )

        delta = "primera creación" if antes is None else f"{antes} → {despues}"
        ok(f"{self.dest_table}: {despues} filas ({delta})")
        return despues