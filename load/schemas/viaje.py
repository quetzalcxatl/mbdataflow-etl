# load/schemas/viaje.py
# -*- coding: utf-8 -*-
"""
Schemas EXPLÍCITOS de BigQuery para las tablas de pipeline_Viaje.

Fuente de verdad: extraídos de INFORMATION_SCHEMA.COLUMNS de las tablas de
producción reales, no inferidos del CSV. El orden de las columnas es
SIGNIFICATIVO: el load job de CSV mapea por POSICIÓN, no por nombre, así que
este orden debe coincidir exactamente con el de la tabla destino.

Por qué explícito y no autodetect:
  - Las columnas del CSV crudo tienen tipos mixtos (visto como DtypeWarning en
    Transform). Autodetect podría inferir distinto entre cargas y romper el
    schema de la tabla, o crear columnas de tipo inconsistente.
  - Un schema explícito es el CONTRATO de la tabla, versionado y revisable.

Si producción cambia el schema de una tabla, este archivo debe actualizarse
para reflejarlo — es el único lugar donde el contrato vive en el repo.

Ver Architecture.md §5.10 (Carga a BigQuery).
"""

from __future__ import annotations

from google.cloud import bigquery


def _f(name: str, field_type: str) -> bigquery.SchemaField:
    """Atajo: todas las columnas son NULLABLE (default de BQ en estas tablas)."""
    return bigquery.SchemaField(name, field_type, mode="NULLABLE")


# --------------------------------------------------------------------------
# centrodecontrol.Sonda.VIAJES  (24 columnas)
# Orden y tipos según INFORMATION_SCHEMA de producción.
# Nota: FECHA es TIMESTAMP (no DATE/DATETIME) — relevante para el DELETE del
# loader, que castea el rango a TIMESTAMP.
# --------------------------------------------------------------------------
VIAJES_SCHEMA: list[bigquery.SchemaField] = [
    _f("FECHA", "TIMESTAMP"),
    _f("RUTA", "STRING"),
    _f("ECONOMICO_PLANIFICADO", "INT64"),
    _f("VEHICULO_REAL", "INT64"),
    _f("CONDUCTOR", "STRING"),
    _f("LLEGADA_AL_PUNTO", "TIME"),
    _f("PARTIDA_PLANEADA", "TIME"),
    _f("PARTIDA_REAL", "TIME"),
    _f("DIFF_PARTIDA", "STRING"),
    _f("HE", "STRING"),
    _f("LLEGADA_PLANEADA", "TIME"),
    _f("LLEGADA_REAL", "TIME"),
    _f("DIFF_LLEGADA", "STRING"),
    _f("TIEMPO_VIAJE", "TIME"),
    _f("KM_REALIZADO", "FLOAT64"),
    _f("VEL_PROMEDIO_KM", "FLOAT64"),
    _f("TIEMPPUNTO", "TIME"),
    _f("PASAJERO", "INT64"),
    _f("IPK", "FLOAT64"),
    _f("STATUS_DEL_VIAJE", "INT64"),
    _f("DESC_STATUS_DEL_VIAJE", "STRING"),
    _f("VIAJE_EDITADO", "STRING"),
    _f("POSICION", "INT64"),
    _f("EMPRESA", "STRING"),
]


# --------------------------------------------------------------------------
# centrodecontrol.TIEMPO_INTERTRAMOS.INTERVALOS_Y_CUMPLIMIENTOS  (13 columnas)
# Orden y tipos según INFORMATION_SCHEMA de producción.
# Notas:
#   - FECHA / PARTIDA_REAL / TIMESTAMP_SALIDA / SALIDA_SIGUIENTE son DATETIME
#     (sin timezone) — el DELETE del loader castea el rango a DATETIME.
#   - ECONOMICO es FLOAT64 (número de autobús como float; paridad con legacy).
#   - PARTIDA_REAL trae fecha basura 1900-01-01 (el Transform parsea solo la
#     hora); es DATETIME válido, el dashboard usa solo la parte de hora.
#   - INTERVALO es STRING (timedelta serializado, ej. "0 days 00:12:30").
# --------------------------------------------------------------------------
INTERVALOS_Y_CUMPLIMIENTOS_SCHEMA: list[bigquery.SchemaField] = [
    _f("FECHA", "DATETIME"),
    _f("RUTA", "STRING"),
    _f("ECONOMICO", "FLOAT64"),
    _f("PARTIDA_REAL", "DATETIME"),
    _f("ESTADO", "STRING"),
    _f("DIFF_PARTIDA", "FLOAT64"),
    _f("TIMESTAMP_SALIDA", "DATETIME"),
    _f("SALIDA_SIGUIENTE", "DATETIME"),
    _f("INTERVALO", "STRING"),
    _f("INTERVALO_MINUTOS", "FLOAT64"),
    _f("DIA_DE_LA_SEMANA", "INT64"),
    _f("TURNO", "STRING"),
    _f("FRANJA_HORARIA", "INT64"),
]