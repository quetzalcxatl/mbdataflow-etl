# load/loaders/bigquery_loader.py
# -*- coding: utf-8 -*-
"""
Loader genérico de CSV procesado -> tabla de BigQuery.

Patrón: DELETE-THEN-APPEND idempotente por rango de fechas.
    1. Valida que el CSV exista, no esté vacío y que sus fechas caigan
       dentro del rango declarado por el pipeline.
    2. DELETE de las filas del rango en la tabla destino.
    3. Load job (batch) del CSV con WRITE_APPEND y schema EXPLÍCITO.
    4. Verifica que las filas insertadas coincidan con las del CSV.

Correr el pipeline N veces sobre la misma semana produce el mismo resultado:
el DELETE limpia el rango antes de reinsertarlo. Esto protege contra reintentos
de Cloud Run y contra ejecuciones manuales encima de la programada.

NO es específico de ningún pipeline. Se parametriza con (csv, tabla, schema,
columna de fecha + su tipo, rango). Cada pipeline que cargue a BQ lo reutiliza.

Ver Architecture.md §5.10 para el racional del patrón.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import google.auth
import pandas as pd
from google.cloud import bigquery

from utils.logger import ok, info, err


# Tipos de columna de fecha soportados para construir el predicado del DELETE.
# El tipo importa: VIAJES.FECHA es TIMESTAMP, INTERVALOS_Y_CUMPLIMIENTOS.FECHA
# es DATETIME. El predicado debe castear el parámetro al tipo de la columna,
# o BigQuery falla con un error de comparación de tipos.
_DATE_CASTS = {
    "TIMESTAMP": "TIMESTAMP",
    "DATETIME": "DATETIME",
    "DATE": "DATE",
}


class BigQueryLoader:
    """
    Carga un CSV procesado a una tabla de BigQuery, idempotente por rango de fechas.

    Args:
        csv_path:         CSV producido por la etapa Transform.
        table_id:         FQN de la tabla, "proyecto.dataset.tabla".
        schema:           Lista de bigquery.SchemaField. EXPLÍCITO, nunca autodetect.
        date_column:      Columna de fecha usada para acotar el DELETE (ej. "FECHA").
        date_column_type: Tipo BQ de esa columna: TIMESTAMP | DATETIME | DATE.
        date_range:       (inicio, fin) inclusivo — la semana vencida que procesa
                          el pipeline. Se usa tanto para la guarda como para el DELETE.
    """

    def __init__(
        self,
        csv_path: Path,
        table_id: str,
        schema: list[bigquery.SchemaField],
        date_column: str,
        date_column_type: str,
        date_range: tuple[date, date],
    ):
        if date_column_type not in _DATE_CASTS:
            raise ValueError(
                f"date_column_type='{date_column_type}' no soportado. "
                f"Opciones: {list(_DATE_CASTS)}"
            )
        self.csv_path = Path(csv_path)
        self.table_id = table_id
        self.schema = schema
        self.date_column = date_column
        self.date_column_type = date_column_type
        self.start, self.end = date_range

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _get_client(self) -> bigquery.Client:
        # ADC: en local usa GOOGLE_APPLICATION_CREDENTIALS, en Cloud Run la SA
        # attached al Job. Mismo patrón que los loaders de Drive (§5.3).
        creds, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/bigquery"]
        )
        return bigquery.Client(credentials=creds, project=project)

    # ------------------------------------------------------------------
    # Guardas previas (§5.6: fallar ruidosamente, nunca silenciosamente)
    # ------------------------------------------------------------------

    def _validate_csv(self) -> int:
        """Valida existencia y contenido. Retorna el número de filas de datos."""
        if not self.csv_path.exists():
            raise FileNotFoundError(f"BQ Load: no existe el CSV: {self.csv_path}")
        if self.csv_path.stat().st_size == 0:
            raise RuntimeError(f"BQ Load: el CSV está vacío: {self.csv_path}")

        df = pd.read_csv(self.csv_path, usecols=[self.date_column])
        n_rows = len(df)
        if n_rows == 0:
            raise RuntimeError(
                f"BQ Load: el CSV no tiene filas de datos: {self.csv_path}. "
                f"Cargar 0 filas tras un DELETE dejaría un hueco en {self.table_id}."
            )
        return n_rows

    def _validate_date_range(self) -> None:
        """
        Verifica que las fechas del CSV caigan dentro del rango declarado.

        CRÍTICO. Sin esta guarda, si el scraper bajó una semana equivocada, el
        DELETE borraría el rango correcto y el INSERT metería otro: la tabla
        histórica quedaría con un hueco permanente y datos duplicados en otra
        semana. Es el peor fallo posible de este loader, y es silencioso.
        """
        dates = pd.read_csv(self.csv_path, usecols=[self.date_column])[self.date_column]
        parsed = pd.to_datetime(dates, errors="coerce")

        n_unparseable = int(parsed.isna().sum())
        if n_unparseable > 0:
            raise RuntimeError(
                f"BQ Load: {n_unparseable} filas con '{self.date_column}' no parseable "
                f"en {self.csv_path.name}. No se puede validar el rango de forma segura."
            )

        csv_min = parsed.min().date()
        csv_max = parsed.max().date()

        if csv_min < self.start or csv_max > self.end:
            raise RuntimeError(
                f"BQ Load: las fechas del CSV se salen del rango declarado. "
                f"CSV=[{csv_min} .. {csv_max}], rango esperado=[{self.start} .. {self.end}]. "
                f"Abortando ANTES del DELETE para no corromper {self.table_id}."
            )

        info(f"Rango validado: CSV=[{csv_min} .. {csv_max}] dentro de [{self.start} .. {self.end}]")

    # ------------------------------------------------------------------
    # DELETE del rango
    # ------------------------------------------------------------------

    def _delete_range(self, client: bigquery.Client) -> int:
        """
        Borra las filas del rango [start, end] en la tabla destino.

        Usa intervalo HALF-OPEN [start, end+1día) en vez de BETWEEN.

        Por qué: VIAJES.FECHA es TIMESTAMP. Un `BETWEEN start AND end` castea
        `end` a medianoche, así que excluiría cualquier fila del último día con
        hora > 00:00:00. Hoy todas las FECHAs son medianoche exacta y BETWEEN
        funcionaría por accidente; el half-open es correcto independientemente
        de eso, y no se rompe si mañana llega una FECHA con hora.

        Idempotente: si el rango no está en la tabla, borra 0 filas y sigue.
        """
        cast = _DATE_CASTS[self.date_column_type]
        query = f"""
            DELETE FROM `{self.table_id}`
            WHERE {self.date_column} >= {cast}(@start)
              AND {self.date_column} <  {cast}(DATE_ADD(@end, INTERVAL 1 DAY))
        """
        # Query parameters, NUNCA f-strings con las fechas: evita inyección y
        # errores de formato de fecha entre locales.
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", self.start),
                bigquery.ScalarQueryParameter("end", "DATE", self.end),
            ]
        )
        job = client.query(query, job_config=job_config)
        job.result()  # bloquea hasta terminar; propaga excepción si falla

        deleted = job.num_dml_affected_rows or 0
        info(f"DELETE en {self.table_id}: {deleted} filas del rango [{self.start} .. {self.end}]")
        return deleted

    # ------------------------------------------------------------------
    # Load job
    # ------------------------------------------------------------------

    def _append_csv(self, client: bigquery.Client) -> int:
        """
        Carga el CSV con WRITE_APPEND y schema explícito.

        Load job (batch), NO streaming inserts: es gratis (no cobra por byte
        cargado), es transaccional a nivel tabla, y no deja filas en el
        streaming buffer —que bloquearían futuros DELETE.

        El CSV lo escribe pandas con to_csv(index=False): delimitador coma,
        UTF-8, header en la primera fila. OJO: es distinto del CSV crudo de
        Sonda (';' + latin1). Este loader carga el PROCESADO.
        """
        job_config = bigquery.LoadJobConfig(
            schema=self.schema,               # explícito: autodetect es una bomba
            autodetect=False,                 # de tiempo con columnas de tipo mixto
            source_format=bigquery.SourceFormat.CSV,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            skip_leading_rows=1,              # header de pandas
            field_delimiter=",",
            encoding="UTF-8",
            allow_quoted_newlines=True,       # campos de texto con saltos de línea
            max_bad_records=0,                # cero tolerancia: una fila mala = fallo
        )

        with self.csv_path.open("rb") as f:
            job = client.load_table_from_file(f, self.table_id, job_config=job_config)
        job.result()  # bloquea; propaga excepción si falla

        inserted = job.output_rows or 0
        info(f"APPEND en {self.table_id}: {inserted} filas insertadas")
        return inserted

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self) -> int:
        """
        Ejecuta delete-then-append. Retorna el número de filas insertadas.

        NOTA sobre transaccionalidad: el DELETE y el APPEND son dos operaciones
        separadas. Si el proceso muere entre ambas, el rango queda borrado y no
        reinsertado. Ventana de riesgo de segundos. Mitigación: re-ejecutar el
        pipeline —el DELETE es idempotente y el APPEND repone. Se acepta este
        riesgo en vez de la complejidad de una tabla staging + swap atómico.
        """
        csv_rows = self._validate_csv()
        self._validate_date_range()

        client = self._get_client()

        self._delete_range(client)
        inserted = self._append_csv(client)

        # El load job con max_bad_records=0 ya falla si una fila es mala, pero
        # verificamos igual: un descarte silencioso aquí sería un hueco de datos.
        if inserted != csv_rows:
            raise RuntimeError(
                f"BQ Load: discrepancia de filas en {self.table_id}. "
                f"CSV tenía {csv_rows}, BigQuery insertó {inserted}. "
                f"Posible descarte silencioso de filas."
            )

        ok(f"{self.table_id}: {inserted} filas cargadas ({self.csv_path.name})")
        return inserted