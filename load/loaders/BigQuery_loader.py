# load/loaders/bigquery_loader.py
# -*- coding: utf-8 -*-
"""
Loader genérico de CSV procesado -> tabla de BigQuery.

Patrón: DELETE-THEN-APPEND idempotente por rango de instantes.
    1. Valida que el CSV exista, no esté vacío y que la KEY SINTÉTICA de sus
       filas caiga dentro del rango DATETIME declarado por el pipeline.
    2. DELETE de las filas cuya key sintética cae en [start, end).
    3. Load job (batch) del CSV con WRITE_APPEND y schema EXPLÍCITO.
    4. Verifica que las filas insertadas coincidan con las del CSV.

Correr el pipeline N veces sobre la misma semana produce el mismo resultado:
el DELETE limpia el rango antes de reinsertarlo. Esto protege contra reintentos
de Cloud Run y contra ejecuciones manuales encima de la programada.

Contrato del rango
------------------
`date_range` es `(start, end)` de datetimes (tz-aware o naive). El intervalo
es SEMIABIERTO: `[start, end)`. La conversión a naive (wall-clock CDMX) ocurre
en el propio loader — Sonda emite timestamps sin timezone y las tablas los
almacenan en el mismo wall-clock, así que la comparación se hace en ese
espacio para evitar corrimientos de zona.

Bajo el contrato de "semana operativa" de Viaje (lunes 03:20 → lunes 03:20)
esto significa que el DELETE remueve exactamente la semana operativa completa,
sin recortar por medianoche.

Por qué key sintética y no FECHA directa
----------------------------------------
En las tablas de Viaje la columna FECHA es de granularidad DÍA — siempre
midnight, es un calendar-date guardado como TIMESTAMP/DATETIME. La marca
temporal real del viaje vive en columnas TIME/DATETIME separadas
(PARTIDA_REAL, PARTIDA_PLANEADA, …). Comparar FECHA directamente contra un
rango datetime-preciso rompe la idempotencia en los lunes frontera: filas
midnight quedan sistemáticamente fuera del DELETE por el borde inferior, se
reinsertan cada corrida, y duplican silenciosamente.

La solución es construir una key sintética que combine
  DATE(FECHA) + TIME(<time_column>)
para reconstruir el instante real del viaje. Guarda y DELETE usan la MISMA
key sintética — asimetría entre ambos sería la puerta a corrupción silenciosa.

Manejo de NULLs en time_column
------------------------------
Sonda representa "sin partida real" (viajes cancelados/no realizados) como
"-" en el CSV, que carga como NULL. Dos consecuencias:

* GUARDA (Python sobre CSV): filas con time_column NULL NO se validan
  contra el rango — se filtran. La guarda protege contra "el scraper bajó
  otra semana" con el resto de las filas.
* DELETE (SQL sobre BQ): NO puede filtrar los NULL o se pierde idempotencia
  para cancelados. Se usa COALESCE(time_column, time_column_fallback,
  TIME '00:00:00') para reconstruir un instante utilizable. En el peor caso
  (ambas TIME columns NULL, muy raro) la fila cae en midnight — puede
  quedar fuera del rango en el lunes frontera. Compromiso documentado.

NO es específico de ningún pipeline. Se parametriza con
(csv, tabla, schema, columna de fecha + tipo, columna de tiempo + fallback,
rango). Cada pipeline que cargue a BQ lo reutiliza.

Ver Architecture.md §5.10 para el racional del patrón.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import google.auth
import pandas as pd
from google.cloud import bigquery

from utils.logger import ok, info, err


# Tipos de columna de fecha soportados. El tipo importa: VIAJES.FECHA es
# TIMESTAMP, INTERVALOS_Y_CUMPLIMIENTOS.FECHA es DATETIME. El parámetro del
# DELETE va como DATETIME (la key sintética siempre es DATETIME) y la
# comparación se hace en ese espacio.
_SUPPORTED_TYPES = frozenset({"TIMESTAMP", "DATETIME", "DATE"})


def _to_naive_cdmx(dt: datetime) -> datetime:
    """
    Convierte un datetime (tz-aware o naive) a naive en wall-clock CDMX.

    Las tablas de BQ (TIMESTAMP y DATETIME) y los CSV procesados almacenan
    FECHAs como wall-clock CDMX sin timezone. Comparaciones y filtros SQL
    ocurren en ese espacio; convertir a naive-CDMX evita corrimientos por
    la interpretación UTC-por-default de TIMESTAMP en BigQuery.
    """
    if dt.tzinfo is None:
        return dt
    import pytz
    return dt.astimezone(pytz.timezone("America/Mexico_City")).replace(tzinfo=None)


class BigQueryLoader:
    """
    Carga un CSV procesado a una tabla de BigQuery, idempotente por rango
    sobre una key sintética DATE(FECHA) + TIME(time_column).

    Args:
        csv_path:              CSV producido por la etapa Transform.
        table_id:              FQN de la tabla, "proyecto.dataset.tabla".
        schema:                Lista de bigquery.SchemaField. EXPLÍCITO.
        date_column:           Columna FECHA (de granularidad día).
        date_column_type:      Tipo BQ: TIMESTAMP | DATETIME | DATE.
        time_column:           Columna TIME/DATETIME cuya parte-hora se
                               combina con DATE(date_column) para formar la
                               key sintética. Ej: "PARTIDA_REAL".
        time_column_fallback:  Opcional. Columna de respaldo para el DELETE
                               cuando time_column es NULL. Ej: "PARTIDA_PLANEADA".
                               NO se usa en la guarda (que ignora NULLs).
        date_range:            (start, end) datetimes; intervalo SEMIABIERTO
                               [start, end). tz-aware o naive; se normaliza a
                               naive-CDMX internamente.
    """

    def __init__(
        self,
        csv_path: Path,
        table_id: str,
        schema: list[bigquery.SchemaField],
        date_column: str,
        date_column_type: str,
        time_column: str,
        date_range: tuple[datetime, datetime],
        time_column_fallback: str | None = None,
    ):
        if date_column_type not in _SUPPORTED_TYPES:
            raise ValueError(
                f"date_column_type='{date_column_type}' no soportado. "
                f"Opciones: {sorted(_SUPPORTED_TYPES)}"
            )
        start, end = date_range
        if end <= start:
            raise ValueError(
                f"BQ Load: rango inválido, end <= start ({start!r} .. {end!r})."
            )
        self.csv_path = Path(csv_path)
        self.table_id = table_id
        self.schema = schema
        self.date_column = date_column
        self.date_column_type = date_column_type
        self.time_column = time_column
        self.time_column_fallback = time_column_fallback
        # Normalizar a naive-CDMX una sola vez para todas las comparaciones y
        # parámetros SQL.
        self.start: datetime = _to_naive_cdmx(start)
        self.end: datetime = _to_naive_cdmx(end)

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
        Verifica que la KEY SINTÉTICA de las filas del CSV caiga en [start, end).

        CRÍTICO. Sin esta guarda, si el scraper bajó una semana equivocada, el
        DELETE borraría el rango correcto y el INSERT metería otro: la tabla
        histórica quedaría con un hueco permanente y datos duplicados en otra
        semana. Es el peor fallo posible de este loader, y es silencioso.

        Filas con `time_column` NULL se IGNORAN en la validación (viajes
        cancelados no participan del check de rango). Si TODAS las filas
        tienen `time_column` NULL, se aborta — no hay evidencia sobre la
        que validar.

        La comparación es a nivel INSTANTE (datetime). Con el contrato 03:20
        esto importa: un viaje que sale a las 03:20:00 del lunes es válido;
        uno que sale a las 03:19:59 del mismo lunes pertenece a la semana
        ANTERIOR y NO debería estar en este CSV.
        """
        df = pd.read_csv(
            self.csv_path,
            usecols=[self.date_column, self.time_column],
            low_memory=False,
        )

        # Ambas columnas parseadas por separado. Los "-" y otros no-parseables
        # se convierten en NaT.
        fecha_parsed = pd.to_datetime(df[self.date_column], errors="coerce")
        time_parsed  = pd.to_datetime(df[self.time_column], errors="coerce", format="mixed")

        # Guarda de calidad sobre FECHA — si FECHA es no-parseable en muchas
        # filas hay un problema estructural en el CSV que no debe quedar mudo.
        n_fecha_bad = int(fecha_parsed.isna().sum())
        if n_fecha_bad > 0:
            raise RuntimeError(
                f"BQ Load: {n_fecha_bad} filas con '{self.date_column}' no "
                f"parseable en {self.csv_path.name}. No se puede validar el "
                f"rango de forma segura."
            )

        # Filas con time_column NULL: se ignoran en la validación por
        # decisión de diseño (cancelados no aportan evidencia de rango).
        mask_valid = time_parsed.notna()
        n_skipped = int((~mask_valid).sum())
        if not mask_valid.any():
            raise RuntimeError(
                f"BQ Load: TODAS las filas tienen '{self.time_column}' NULL en "
                f"{self.csv_path.name}. No hay evidencia para validar el rango."
            )

        # Key sintética: fecha-parte de FECHA + hora-parte de time_column.
        # Se construye como string y se re-parsea, para evitar sorpresas de
        # aritmética de Timestamps entre versiones de pandas.
        fecha_str = fecha_parsed[mask_valid].dt.strftime("%Y-%m-%d")
        time_str  = time_parsed[mask_valid].dt.strftime("%H:%M:%S")
        key = pd.to_datetime(fecha_str + " " + time_str)

        csv_min: datetime = key.min().to_pydatetime()
        csv_max: datetime = key.max().to_pydatetime()

        # Semi-abierto [start, end): csv_max debe ser ESTRICTAMENTE < end.
        if csv_min < self.start or csv_max >= self.end:
            raise RuntimeError(
                f"BQ Load: la key sintética del CSV se sale del rango declarado.\n"
                f"  key = DATE({self.date_column}) + TIME({self.time_column})\n"
                f"  csv=[{csv_min} .. {csv_max}]  rango=[{self.start} .. {self.end})\n"
                f"  filas ignoradas por {self.time_column} NULL: {n_skipped}\n"
                f"Abortando ANTES del DELETE para no corromper {self.table_id}."
            )

        info(
            f"Rango validado: key=[{csv_min} .. {csv_max}] dentro de "
            f"[{self.start} .. {self.end}) "
            f"(ignoradas {n_skipped} filas con {self.time_column} NULL)"
        )

    # ------------------------------------------------------------------
    # DELETE del rango
    # ------------------------------------------------------------------

    def _synthetic_key_sql(self) -> str:
        """
        Construye la expresión SQL de la key sintética.

            DATETIME(
              DATE(<FECHA>),
              COALESCE(TIME(<time_column>),
                       [TIME(<fallback>),]
                       TIME '00:00:00')
            )

        `TIME(...)` funciona uniformemente sobre TIME (identidad) y sobre
        DATETIME (extrae la parte-hora, ignorando la fecha dummy). Eso permite
        que la misma expresión sirva para VIAJES (PARTIDA_REAL es TIME) y para
        INTERVALOS_Y_CUMPLIMIENTOS (PARTIDA_REAL es DATETIME con fecha basura
        1900-01-01, solo la parte-hora es válida).
        """
        coalesce_args = [f"TIME(`{self.time_column}`)"]
        if self.time_column_fallback:
            coalesce_args.append(f"TIME(`{self.time_column_fallback}`)")
        coalesce_args.append("TIME '00:00:00'")

        return (
            f"DATETIME("
            f"DATE(`{self.date_column}`), "
            f"COALESCE({', '.join(coalesce_args)})"
            f")"
        )

    def _delete_range(self, client: bigquery.Client) -> int:
        """
        Borra las filas cuya key sintética cae en [start, end).

        Semi-abierto en el instante: `end` es el borde de cierre exclusivo.
        Bajo el contrato 03:20, `start` y `end` son ambos "lunes 03:20:00" —
        la resta 7 días exacta.

        NULLs en `time_column`: se resuelven vía COALESCE en la key sintética.
        Sin esto, filas con time_column NULL (cancelados) no serían borradas y
        se duplicarían en cada corrida. Es el punto crítico del contrato.

        Idempotente: si el rango no está en la tabla, borra 0 filas y sigue.
        """
        key_expr = self._synthetic_key_sql()
        query = f"""
            DELETE FROM `{self.table_id}`
            WHERE {key_expr} >= @start
              AND {key_expr} <  @end
        """
        # Ambos params como DATETIME: la key sintética es DATETIME sin
        # importar el tipo original de la columna FECHA.
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATETIME", self.start),
                bigquery.ScalarQueryParameter("end",   "DATETIME", self.end),
            ]
        )
        job = client.query(query, job_config=job_config)
        job.result()  # bloquea; propaga excepción si falla

        deleted = job.num_dml_affected_rows or 0
        info(
            f"DELETE en {self.table_id}: {deleted} filas del rango "
            f"[{self.start} .. {self.end})"
        )
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