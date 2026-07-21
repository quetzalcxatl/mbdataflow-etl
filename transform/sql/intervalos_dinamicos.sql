-- transform/sql/intervalos_dinamicos.sql
--
-- INTERVALOSDINAMICOS — capa de transformación POST-CARGA en BigQuery.
-- Reconstruye la tabla de intervalos PROGRAMADOS agregados por FECHA/RUTA/HORA
-- a partir de la tabla de viajes.
--
-- Ejecutada por: transform/bq_sql_runner.py
-- Depende de:    que el BigQueryLoader ya haya cargado la semana en la tabla
--                fuente. Si corre antes, reconstruye sin los datos frescos.
--
-- PLACEHOLDERS (sustituidos por el runner; NO hardcodear FQN — repo público).
-- Se nombran aquí sin llaves a propósito, para que estos comentarios NO se
-- sustituyan al renderizar:
--   source_table  -> tabla de viajes      (ej. <project>.<dataset>.VIAJES)
--   dest_table    -> tabla de intervalos  (ej. <project>.<dataset>.INTERVALOS)
--
-- PARIDAD CON EL PROCESO MANUAL: esta query reproduce sin cambios la que se
-- ejecutaba a mano cada semana. Su comportamiento se documenta aquí pero NO se
-- corrige, para no alterar el dashboard de Looker Studio que consume
-- INTERVALO_SEC como insumo de un campo calculado (adelanto/atraso de unidad).
--
-- Comportamiento documentado (decisiones heredadas, no bugs a arreglar hoy):
--   1. Usa PARTIDA_PLANEADA, no PARTIDA_REAL. Mide la frecuencia PROGRAMADA,
--      no la operada. Es una métrica de planeación.
--   2. No filtra STATUS_DEL_VIAJE. Como la rama VIAJE del Transform sube todos
--      los estatus, aquí entran viajes cancelados o no realizados que sí tenían
--      partida programada.
--   3. INTERVALO_SEC = MAX(gap) / 2 dentro de cada FECHA/RUTA/HORA. Es el hueco
--      MÁXIMO de esa hora partido a la mitad, no el headway promedio.
--   4. CREATE OR REPLACE reconstruye desde TODO el histórico en cada corrida.
--      Atómico en BigQuery: la tabla anterior permanece disponible hasta que la
--      nueva está lista, así que un fallo a media query no deja hueco.
--      Costo: escanea {source_table} completa cada semana (deuda conocida;
--      ver Architecture.md §8).

CREATE OR REPLACE TABLE `{dest_table}` AS

WITH departures AS (
  SELECT
    DATE(FECHA)                                        AS FECHA,
    RUTA,
    PARTIDA_PLANEADA,
    -- Combina fecha y hora en un timestamp real para poder calcular diferencias.
    TIMESTAMP(DATETIME(DATE(FECHA), PARTIDA_PLANEADA)) AS ts
  FROM
    `{source_table}`
  WHERE
    PARTIDA_PLANEADA IS NOT NULL
),

gaps AS (
  SELECT
    FECHA,
    RUTA,
    EXTRACT(HOUR FROM ts) AS HORA,
    -- Segundos transcurridos desde la salida anterior de la misma FECHA/RUTA.
    TIMESTAMP_DIFF(
      ts,
      LAG(ts) OVER (PARTITION BY FECHA, RUTA ORDER BY ts),
      SECOND
    )                     AS diff_sec
  FROM
    departures
)

SELECT
  FECHA,
  RUTA,
  HORA,
  -- La mitad del hueco máximo observado en esa hora (en segundos).
  MAX(diff_sec) / 2 AS INTERVALO_SEC
FROM
  gaps
WHERE
  diff_sec IS NOT NULL   -- descarta la primera salida de cada FECHA/RUTA (LAG nulo)
GROUP BY
  FECHA, RUTA, HORA
ORDER BY
  FECHA, RUTA, HORA;