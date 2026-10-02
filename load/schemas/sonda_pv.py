# load/schemas/sonda_pv.py
# -*- coding: utf-8 -*-
"""
Schema EXPLÍCITO de BigQuery para la tabla de pipeline_PV.

Fuente de verdad: INFORMATION_SCHEMA.COLUMNS de la tabla de producción
centrodecontrol.Sonda.PV (consultado 2026-10-02), no inferido del CSV.

El orden de las columnas es SIGNIFICATIVO: el load job de CSV mapea por
POSICIÓN, no por nombre. El transform de PV escribe el CSV procesado en este
mismo orden (lo deriva de PV_COLUMNS), así que este archivo es el único lugar
donde vive el contrato de la tabla.

Notas:
  - `date` es DATETIME de granularidad DÍA (siempre midnight); es la llave del
    DELETE idempotente de BigQueryDayLoader.
  - `economico` es STRING: el transform lee el CSV crudo con dtype=str para no
    convertir "1234" en "1234.0" cuando la columna trae vacíos.

Ver Architecture.md §5.10 (Carga a BigQuery).
"""

from __future__ import annotations

from google.cloud import bigquery


def _f(name: str, field_type: str) -> bigquery.SchemaField:
    """Atajo: todas las columnas son NULLABLE (default de BQ en esta tabla)."""
    return bigquery.SchemaField(name, field_type, mode="NULLABLE")


# --------------------------------------------------------------------------
# centrodecontrol.Sonda.PV  (7 columnas)
# --------------------------------------------------------------------------
PV_SCHEMA: list[bigquery.SchemaField] = [
    _f("date", "DATETIME"),
    _f("turno", "STRING"),
    _f("ruta", "STRING"),
    _f("jornada", "INT64"),
    _f("economico", "STRING"),
    _f("empresa_programado", "STRING"),
    _f("empresa_real", "STRING"),
]

# Orden de columnas del CSV procesado, derivado del schema para que no diverjan.
PV_COLUMNS: list[str] = [field.name for field in PV_SCHEMA]
