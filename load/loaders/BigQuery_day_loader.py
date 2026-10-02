# load/loaders/BigQuery_day_loader.py
# -*- coding: utf-8 -*-
"""
Variante de BigQueryLoader para tablas cuya unidad de carga es UN DÍA completo.

Patrón: DELETE-THEN-APPEND idempotente por fecha (mismo contrato que
BigQueryLoader, ver Architecture.md §5.10):
    1. Valida que el CSV exista y no esté vacío              (heredado)
    2. Valida que TODAS sus filas sean de la fecha declarada
       y que traiga TODAS las particiones esperadas (ej. ambos turnos)
    3. DELETE de las filas de esa fecha
    4. Load job del CSV con WRITE_APPEND y schema explícito  (heredado)
    5. Verifica filas insertadas == filas del CSV            (heredado)

Por qué una subclase y no BigQueryLoader directo
------------------------------------------------
BigQueryLoader construye su key sintética DATE(FECHA) + TIME(time_column) y
borra un RANGO de instantes. Tablas como Sonda.PV no tienen columna de hora:
la fecha es de granularidad día y la corrida carga el día entero. Aquí la
llave es simplemente DATE(date_column) = @fecha.

Solo se sobreescriben la guarda de rango y el DELETE; el load job, la
autenticación y la verificación de conteo son los del loader base, que ya está
validado en producción (pipeline_Viaje). BigQueryLoader no se modifica.

Guarda de particiones (por qué importa)
---------------------------------------
El DELETE borra el DÍA COMPLETO. Si el CSV trajera solo un turno (descarga a
medias, cambio de contrato del scraper), el DELETE borraría ambos turnos y el
APPEND repondría uno: hueco silencioso. Por eso, antes del DELETE, se exige que
el CSV contenga exactamente las particiones esperadas, cada una con filas.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from google.cloud import bigquery

from load.loaders.BigQuery_loader import BigQueryLoader
from utils.logger import info


class BigQueryDayLoader(BigQueryLoader):
    """
    Carga un CSV procesado de UN día a BigQuery, idempotente por fecha.

    Args:
        csv_path:            CSV producido por la etapa Transform.
        table_id:            FQN de la tabla, "proyecto.dataset.tabla".
        schema:              Lista de bigquery.SchemaField. EXPLÍCITO.
        date_column:         Columna de fecha (granularidad día).
        date_column_type:    Tipo BQ: TIMESTAMP | DATETIME | DATE.
        load_date:           Fecha que carga esta corrida (calendar date CDMX).
        partition_column:    Opcional. Columna cuyas particiones deben venir
                             completas en el CSV. Ej: "turno".
        expected_partitions: Valores que deben aparecer en partition_column,
                             todos con al menos una fila. Ej: {"Matutino", "Vespertino"}.
    """

    def __init__(
        self,
        csv_path: Path,
        table_id: str,
        schema: list[bigquery.SchemaField],
        date_column: str,
        date_column_type: str,
        load_date: date,
        partition_column: str | None = None,
        expected_partitions: set[str] | None = None,
    ):
        if (partition_column is None) != (expected_partitions is None):
            raise ValueError(
                "BQ Day Load: partition_column y expected_partitions van juntos "
                "(ambos o ninguno)."
            )
        day_start = datetime(load_date.year, load_date.month, load_date.day)
        # El rango [día, día+1) solo alimenta el estado y los mensajes del loader
        # base; la guarda y el DELETE de esta clase trabajan con load_date.
        super().__init__(
            csv_path=csv_path,
            table_id=table_id,
            schema=schema,
            date_column=date_column,
            date_column_type=date_column_type,
            time_column="",
            date_range=(day_start, day_start + timedelta(days=1)),
        )
        self.load_date = load_date
        self.partition_column = partition_column
        self.expected_partitions = set(expected_partitions or ())

    # ------------------------------------------------------------------
    # Guarda previa: fecha única + particiones completas
    # ------------------------------------------------------------------
    def _validate_date_range(self) -> None:
        usecols = [self.date_column]
        if self.partition_column:
            usecols.append(self.partition_column)
        df = pd.read_csv(self.csv_path, usecols=usecols, dtype=str)

        fechas = pd.to_datetime(df[self.date_column], errors="coerce")
        n_bad = int(fechas.isna().sum())
        if n_bad:
            raise RuntimeError(
                f"BQ Day Load: {n_bad} filas con '{self.date_column}' no parseable "
                f"en {self.csv_path.name}. Abortando ANTES del DELETE."
            )

        dias = set(fechas.dt.date.unique())
        if dias != {self.load_date}:
            raise RuntimeError(
                f"BQ Day Load: el CSV trae fechas {sorted(dias)} y la corrida declara "
                f"{self.load_date}. Abortando ANTES del DELETE para no corromper "
                f"{self.table_id}."
            )

        if self.partition_column:
            conteo = df[self.partition_column].value_counts().to_dict()
            presentes = set(conteo)
            if presentes != self.expected_partitions:
                raise RuntimeError(
                    f"BQ Day Load: particiones de '{self.partition_column}' incompletas "
                    f"o inesperadas en {self.csv_path.name}: "
                    f"presentes={sorted(presentes)}, esperadas={sorted(self.expected_partitions)}. "
                    f"El DELETE borra el día completo; cargar solo una parte dejaría "
                    f"un hueco. Abortando ANTES del DELETE."
                )
            info(f"Particiones validadas ({self.partition_column}): {conteo}")

        info(f"Fecha validada: todas las filas son de {self.load_date}")

    # ------------------------------------------------------------------
    # DELETE del día
    # ------------------------------------------------------------------
    def _delete_range(self, client: bigquery.Client) -> int:
        """
        Borra las filas de load_date. Idempotente: si el día no está, borra 0.

        DATE() funciona igual sobre DATE, DATETIME y TIMESTAMP. Para TIMESTAMP
        DATE() usa UTC; las tablas de Sonda guardan wall-clock CDMX a midnight,
        así que la fecha no se corre (ver _to_naive_cdmx en BigQueryLoader).
        """
        query = f"""
            DELETE FROM `{self.table_id}`
            WHERE DATE(`{self.date_column}`) = @load_date
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("load_date", "DATE", self.load_date),
            ]
        )
        job = client.query(query, job_config=job_config)
        job.result()  # bloquea; propaga excepción si falla

        deleted = job.num_dml_affected_rows or 0
        info(f"DELETE en {self.table_id}: {deleted} filas de {self.load_date}")
        return deleted
