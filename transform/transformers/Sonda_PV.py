"""
Sonda_PV.py — Etapa Transform del pipeline_PV (MBDataFlow_ETL).

Reproduce el transform del job legacy (mbdataflow_2, connectors/sonda_pv.py):
  - agrega `date` (día de la corrida) y `turno` a cada CSV crudo,
  - convierte `Jornada` a entero,
  - renombra columnas a los nombres de centrodecontrol.Sonda.PV,
y escribe UN CSV procesado con ambos turnos en el orden exacto del schema
(load/schemas/sonda_pv.py), porque el load job mapea por POSICIÓN.

Duplicados: Sonda a veces emite filas idénticas dentro del mismo reporte
(observado en 2026-02-14 y 2026-03-21). Se CONSERVAN — no sabemos si son
error de Sonda o filas legítimas — y se reportan en el log de cada corrida.
"""

from __future__ import annotations

import glob
import os
from datetime import date
from pathlib import Path

import pandas as pd

from config.settings import (
    RAW_PV_PATH,
    PROCESSED_PV_PATH,
)
from load.schemas.sonda_pv import PV_COLUMNS
from utils.logger import info

# Columnas del CSV crudo de Sonda -> columnas de la tabla
PV_RENAME = {
    "Ruta": "ruta",
    "Jornada": "jornada",
    "Económico": "economico",
    "Empresa Programado": "empresa_programado",
    "Empresa Real": "empresa_real",
}


def _read_raw(raw_csv: Path) -> pd.DataFrame:
    if not raw_csv.exists():
        raise FileNotFoundError(f"Transform PV: no existe el CSV crudo: {raw_csv}")
    if raw_csv.stat().st_size == 0:
        raise RuntimeError(f"Transform PV: el CSV crudo está vacío: {raw_csv}")

    # dtype=str: conserva el texto tal cual (economico es STRING en BQ; sin esto
    # una columna numérica con vacíos saldría como "1234.0").
    df = pd.read_csv(raw_csv, sep=";", dtype=str)

    faltantes = [c for c in PV_RENAME if c not in df.columns]
    if faltantes:
        raise RuntimeError(
            f"Transform PV: columnas faltantes en {raw_csv.name}: {faltantes}. "
            f"Columnas recibidas: {list(df.columns)}. Posible cambio de formato en Sonda."
        )
    if len(df) == 0:
        raise RuntimeError(f"Transform PV: {raw_csv.name} no tiene filas de datos.")
    return df


def transform(raw_csvs: dict[str, Path], fecha: date) -> Path:
    """
    Combina los CSV crudos de cada turno en un CSV procesado.

    Args:
        raw_csvs: {turno: Path del CSV crudo} tal como lo devuelve SondaPV_Scraper.
        fecha:    Día de la corrida (calendar date CDMX).

    Returns:
        Path del CSV procesado: PROCESSED_PV_PATH/PV_<YYYYMMDD>.csv
    """
    # DATETIME de BQ acepta 'YYYY-MM-DD HH:MM:SS'. Se escribe como texto explícito:
    # pandas omite la hora al serializar datetimes que son todos midnight.
    date_value = f"{fecha.isoformat()} 00:00:00"

    frames = []
    for turno, raw_csv in raw_csvs.items():
        df = _read_raw(raw_csv).rename(columns=PV_RENAME)
        df["date"] = date_value
        df["turno"] = turno
        info(f"{turno}: {len(df)} filas ({raw_csv.name})")
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    jornada_raw = df["jornada"]
    df["jornada"] = pd.to_numeric(jornada_raw, errors="coerce").astype("Int64")
    n_coerced = int((df["jornada"].isna() & jornada_raw.notna()).sum())
    if n_coerced:
        info(f"jornada: {n_coerced} valores no numéricos quedaron NULL")

    df = df[PV_COLUMNS]

    n_dup = int(df.duplicated().sum())
    if n_dup:
        info(f"Duplicados: {n_dup} filas idénticas en el reporte de Sonda "
             f"(se conservan; ver docstring)")

    out_path = PROCESSED_PV_PATH / f"PV_{fecha.strftime('%Y%m%d')}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return out_path


if __name__ == "__main__":
    # Test manual: toma los crudos más recientes de cada turno en RAW_PV_PATH.
    from utils.dates import today_cdmx

    fecha = today_cdmx().date()
    raw = {}
    for turno in ("Matutino", "Vespertino"):
        matches = glob.glob(str(RAW_PV_PATH / f"PV_*_{turno}.csv"))
        if not matches:
            raise SystemExit(f"No hay CSV PV_*_{turno}.csv en {RAW_PV_PATH}")
        raw[turno] = Path(max(matches, key=os.path.getmtime))
    print(f"[test manual] procesando: {[p.name for p in raw.values()]}")
    print(f"PV procesado: {transform(raw, fecha)}")
