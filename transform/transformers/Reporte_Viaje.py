"""
transform_viaje.py — Etapa Transform del pipeline_Viaje (MBDataFlow_ETL).

Consolida los DOS flujos ACTIVOS de los notebooks legacy de Colab:

  1. CARGADOR_DE_VIAJES_SONDA.py   -> centrodecontrol.Sonda.VIAJES
  2. BigQuery_SONDA_Intervalos.py  -> centrodecontrol.TIEMPO_INTERTRAMOS.INTERVALOS_Y_CUMPLIMIENTOS

Lee los CSV crudos que deja el scraper (Reporte_Viaje.py), produce dos CSV
transformados en PROCESSED_VIAJE_PATH/{VIAJE, INTERVALOS_Y_CUMPLIMIENTOS}/ y
devuelve sus rutas.

Principio de la migración: REPRODUCIR el comportamiento de producción para no
alterar los tableros existentes. Los puntos donde el código legacy es cuestionable
están marcados con  # [REVISAR]  y aislados para poder corregirlos vía ADR.

Puntos a cablear con las constantes reales del repo (utils/paths, _runtime_path):
    RAW_VIAJE_PATH, PROCESSED_VIAJE_PATH.
"""

from __future__ import annotations

import glob
import os
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import (
    RAW_VIAJE_PATH,
    PROCESSED_VIAJE_PATH,
) 

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
SUBFOLDER_VIAJES = "VIAJE"
SUBFOLDER_INTERVALOS = "INTERVALOS_Y_CUMPLIMIENTOS"


# [DECISIÓN] Filtrado por IsolationForest en la rama de intervalos.
#   True  = reproduce producción (conserva ~99% inliers, descarta ~1%).
#   False = no filtra; el recorte de anomalías se delega a la vista/consulta.
APPLY_ISOLATION_FOREST = True
ISOLATION_FOREST_CONTAMINATION = 0.01

# 24 columnas destino de Sonda.VIAJES (orden y nombres del schema).
VIAJES_COLUMNS = [
    "FECHA", "RUTA", "ECONOMICO_PLANIFICADO", "VEHICULO_REAL", "CONDUCTOR",
    "LLEGADA_AL_PUNTO", "PARTIDA_PLANEADA", "PARTIDA_REAL", "DIFF_PARTIDA", "HE",
    "LLEGADA_PLANEADA", "LLEGADA_REAL", "DIFF_LLEGADA", "TIEMPO_VIAJE",
    "KM_REALIZADO", "VEL_PROMEDIO_KM", "TIEMPPUNTO", "PASAJERO", "IPK",
    "STATUS_DEL_VIAJE", "DESC_STATUS_DEL_VIAJE", "VIAJE_EDITADO", "POSICION", "EMPRESA",
]

VIAJES_RENAME = {
    "Fecha": "FECHA", "Ruta": "RUTA", "Económico planificado": "ECONOMICO_PLANIFICADO",
    "Vehiculo real": "VEHICULO_REAL", "Conductor": "CONDUCTOR",
    "Llegada al punto": "LLEGADA_AL_PUNTO", "Partida Planeada": "PARTIDA_PLANEADA",
    "Partida Real": "PARTIDA_REAL", "Diff Partida": "DIFF_PARTIDA", "HE": "HE",
    "Llegada Planeada": "LLEGADA_PLANEADA", "Llegada Real": "LLEGADA_REAL",
    "Diff llegada": "DIFF_LLEGADA", "Tiempo Viaje": "TIEMPO_VIAJE",
    "KM Realizado": "KM_REALIZADO", "Vel. promedio Km": "VEL_PROMEDIO_KM",
    "Tiemp.Punto": "TIEMPPUNTO", "Pasajero": "PASAJERO", "I.P.K": "IPK",
    "Status del Viaje": "STATUS_DEL_VIAJE", "Desc. Status del Viaje": "DESC_STATUS_DEL_VIAJE",
    "Viaje Editado": "VIAJE_EDITADO", "Jornada": "POSICION", "Empresa": "EMPRESA",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _remove_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c)
    )


def _normalize_columns(cols) -> list[str]:
    """UPPER + sin acentos + no-alfanumérico -> '_' (misma norma que el load legacy)."""
    out = [re.sub(r"[^0-9a-zA-Z_]+", "_", _remove_accents(c).upper()) for c in cols]
    return ["_" + c if re.match(r"^[0-9]", c) else c for c in out]


def _diff_str_to_minutes(series: pd.Series, invert_sign: bool) -> np.ndarray:
    """
    Convierte 'HH:MM:SS' (con posible '-') a minutos con signo.

    invert_sign=True reproduce la convención de Diff Partida / HE / Diff llegada:
        el string empieza con '-'  -> POSITIVO ; en caso contrario -> NEGATIVO
    invert_sign=False es la convención natural (Tiempo Viaje):
        el string empieza con '-'  -> NEGATIVO ; en caso contrario -> POSITIVO
    """
    s = series.astype(str)
    parts = s.str.extract(r"([+-]?)(\d+):(\d+):(\d+)", expand=True)
    h = parts[1].fillna(0).astype(int)
    m = parts[2].fillna(0).astype(int)
    sec = parts[3].fillna(0).astype(int)
    minutes = h * 60 + m + sec / 60
    starts_neg = s.str.startswith("-")
    if invert_sign:
        return np.where(starts_neg, minutes, -minutes)
    return np.where(starts_neg, -minutes, minutes)

def _derive_output_paths(raw_csv: Path) -> tuple[Path, Path]:
    """
    Deriva los paths de salida a partir del stem del raw.

    Ejemplo:
        raw_csv = RAW_VIAJE_PATH / "RV_300626_060726.csv"
        stem    = "RV_300626_060726"
        suffix  = "300626_060726"   (sin el prefijo RV_)

        viaje_out       = PROCESSED_VIAJE_PATH / "VIAJE" / "VIAJE_300626_060726.csv"
        intervalos_out  = PROCESSED_VIAJE_PATH / "INTERVALOS_Y_CUMPLIMIENTOS" /
                          "INTERVALOS_Y_CUMPLIMIENTOS_300626_060726.csv"
    """
    stem = raw_csv.stem                          # "RV_300626_060726"
    suffix = stem.removeprefix("RV_")            # "300626_060726"
    viaje_out = PROCESSED_VIAJE_PATH / SUBFOLDER_VIAJES / f"{SUBFOLDER_VIAJES}_{suffix}.csv"
    intervalos_out = (
        PROCESSED_VIAJE_PATH / SUBFOLDER_INTERVALOS / f"{SUBFOLDER_INTERVALOS}_{suffix}.csv"
    )
    return viaje_out, intervalos_out


# --------------------------------------------------------------------------
# Rama VIAJES -> Sonda.VIAJES
# --------------------------------------------------------------------------
def transform_viajes(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Reproduce CARGADOR_DE_VIAJES_SONDA.py (ruta activa).

    Fidelidad de producción:
      - NO filtra por STATUS_DEL_VIAJE (sube todos los estatus).
      - NO deduplica.  # [REVISAR] prod no deduplicaba -> posibles filas repetidas.
      - Las columnas DIFF_* se conservan como STRING crudo, sin convertir a minutos.
    """
    df = df_raw.rename(columns=VIAJES_RENAME).copy()

    df["FECHA"] = pd.to_datetime(df["FECHA"], format="%d/%m/%Y", errors="coerce")
    df["ECONOMICO_PLANIFICADO"] = (
        pd.to_numeric(df["ECONOMICO_PLANIFICADO"], errors="coerce").fillna(0).astype(int)
    )
    df["VEHICULO_REAL"] = (
        pd.to_numeric(df["VEHICULO_REAL"], errors="coerce").fillna(0).astype(int)
    )
    for col in [
        "LLEGADA_AL_PUNTO", "PARTIDA_PLANEADA", "PARTIDA_REAL",
        "LLEGADA_PLANEADA", "LLEGADA_REAL", "TIEMPO_VIAJE", "TIEMPPUNTO",
    ]:
        df[col] = pd.to_datetime(df[col], format="%H:%M:%S", errors="coerce").dt.time
    df["KM_REALIZADO"] = pd.to_numeric(df["KM_REALIZADO"], errors="coerce").fillna(0).astype(float)
    df["VEL_PROMEDIO_KM"] = pd.to_numeric(df["VEL_PROMEDIO_KM"], errors="coerce").fillna(0).astype(float)
    df["PASAJERO"] = pd.to_numeric(df["PASAJERO"], errors="coerce").fillna(0).astype(int)
    df["IPK"] = pd.to_numeric(df["IPK"], errors="coerce").fillna(0).astype(float)
    # prod usaba .astype(int) sin coerce (frágil); se agrega coerce por robustez.
    df["STATUS_DEL_VIAJE"] = pd.to_numeric(df["STATUS_DEL_VIAJE"], errors="coerce").fillna(0).astype(int)
    df["POSICION"] = pd.to_numeric(df["POSICION"], errors="coerce").fillna(0).astype(int)

    return df[VIAJES_COLUMNS]


# --------------------------------------------------------------------------
# Rama INTERVALOS -> INTERVALOS_Y_CUMPLIMIENTOS
# --------------------------------------------------------------------------
def _filter_outliers_isolation_forest(
        interval_df: pd.DataFrame, route_col: str, contamination: float) -> pd.DataFrame:
    """
    Reproduce el paso de IsolationForest por ruta del script de intervalos.

    # [REVISAR] En producción la variable se llamaba `df_outliers`, pero el filtro
    #   `predict == 1` conserva los INLIERS (~99% normales) y descarta el ~1%
    #   anómalo. Es decir: NO sube outliers, sube los datos limpios.
    # [REVISAR] El modelo usa TODAS las columnas numéricas, incluidas ECONOMICO,
    #   DIA_DE_LA_SEMANA y FRANJA_HORARIA, que no tienen semántica de anomalía.
    # [DESVIACIÓN] Se fija random_state para que la salida sea DETERMINISTA
    #   (prod no lo fijaba -> resultados no reproducibles entre corridas).
    """
    from sklearn.ensemble import IsolationForest

    kept = []
    for _route, grp in interval_df.groupby(route_col):
        num = grp.select_dtypes(include=["float64", "int64"])
        if len(grp) < 2 or num.empty:
            kept.append(grp)  # grupo demasiado chico para modelar; se conserva completo
            continue
        model = IsolationForest(contamination=contamination, random_state=42)
        pred = model.fit_predict(num)
        kept.append(grp[pred == 1])  # inliers
    return pd.concat(kept) if kept else interval_df.iloc[0:0]


def transform_intervalos(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Reproduce BigQuery_SONDA_Intervalos.py (ruta activa).

    Mide el HEADWAY (minutos entre salidas consecutivas de la misma Ruta el mismo
    día) más el estado de puntualidad de cada despacho.

    Poda: se omiten los cálculos del legacy que NO afectan a las columnas cargadas
    (parseo de HE, Diff llegada, Tiempo Viaje, KM, Vel, Pasajero, IPK, Linea).
    """
    df = df_raw.copy()

    # Fidelidad: solo viajes realizados (Status == 1), luego dedup.
    df = df[df["Status del Viaje"] == 1].rename(columns={"Vehiculo real": "Economico"})
    df = df.drop_duplicates().reset_index(drop=True)


    df["Fecha"] = pd.to_datetime(df["Fecha"], format="%d/%m/%Y", errors="coerce")
    df["Economico"] = pd.to_numeric(df["Economico"], downcast="integer", errors="coerce")
    df["Partida Real"] = pd.to_datetime(df["Partida Real"], format="%H:%M:%S", errors="coerce")

    # Diff Partida -> minutos con signo INVERTIDO (convención de producción).
    df["Diff Partida"] = _diff_str_to_minutes(df["Diff Partida"], invert_sign=True)

    # Estado de puntualidad (umbral +/-2 min).
    df["Estado"] = df["Diff Partida"].apply(
        lambda x: "En tiempo" if abs(x) <= 2 else ("Adelanta" if x > 2 else "Retrasa")
    )

    # --- Cálculo de intervalos (headway) ---
    cols = ["Fecha", "Ruta", "Economico", "Partida Real", "Estado", "Diff Partida"]
    iv = df[cols].copy()
    iv["Timestamp Salida"] = pd.to_datetime(
        iv["Fecha"].dt.strftime("%Y-%m-%d") + " " + iv["Partida Real"].dt.strftime("%H:%M:%S"),
        errors="coerce",
    )
    iv = iv.sort_values(["Fecha", "Timestamp Salida"])
    iv["Salida siguiente"] = iv.groupby(["Fecha", "Ruta"])["Timestamp Salida"].shift(-1)
    iv["Intervalo"] = iv["Salida siguiente"] - iv["Timestamp Salida"]
    iv["Intervalo Minutos"] = iv["Intervalo"].dt.total_seconds() / 60
    # [REVISAR] prod deja comentados los filtros 0 <= intervalo <= 60:
    #   pasan intervalos negativos (desorden) y enormes (primer despacho / huecos).
    iv["Dia de la semana"] = iv["Fecha"].dt.day_of_week
    iv["Turno"] = pd.cut(
        iv["Partida Real"].dt.hour, bins=[5, 10, 16, 24],
        labels=["Matutino", "Intermedio", "Vespertino"],
    )
    # [REVISAR] horas <= 5 caen en NaN -> quedan como 'nan' tras astype(str).
    iv["Franja Horaria"] = iv["Partida Real"].dt.hour

    # Tipado (igual que prod).
    iv["Ruta"] = iv["Ruta"].astype(str)
    iv["Economico"] = iv["Economico"].astype(float)
    iv["Estado"] = iv["Estado"].astype(str)
    iv["Intervalo Minutos"] = iv["Intervalo Minutos"].astype(float)
    iv["Turno"] = iv["Turno"].astype(str)

    # dropna global (reproduce prod): elimina última salida por grupo, económicos
    # no numéricos, etc. Salida siguiente = NaT en la última fila de cada grupo.
    iv = iv.dropna().copy()

    iv["Intervalo"] = iv["Intervalo"].astype(str)
    iv["Dia de la semana"] = iv["Dia de la semana"].astype(int)
    iv["Franja Horaria"] = iv["Franja Horaria"].astype(int)

    if APPLY_ISOLATION_FOREST:
        iv = _filter_outliers_isolation_forest(
            iv, "Ruta", ISOLATION_FOREST_CONTAMINATION
        )

    iv.columns = _normalize_columns(iv.columns)
    return iv


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def _write_csv(df: pd.DataFrame, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return out_path


def transform(raw_csv: Path) -> tuple[Path, Path]:
    """
    Transforma el CSV crudo de Viaje en dos CSV procesados.

    Args:
        raw_csv: Path al CSV crudo que produjo Extract
                 (ej. RAW_VIAJE_PATH/RV_300626_060726.csv).

    Returns:
        (viaje_path, intervalos_path) — paths absolutos de los CSV escritos
        en PROCESSED_VIAJE_PATH/VIAJE/ y PROCESSED_VIAJE_PATH/INTERVALOS_Y_CUMPLIMIENTOS/.
    """
    df_raw = pd.read_csv(raw_csv, encoding="latin1", sep=";")

    df_viajes = transform_viajes(df_raw)
    df_intervalos = transform_intervalos(df_raw)

    viaje_out, intervalos_out = _derive_output_paths(raw_csv)
    return (
        _write_csv(df_viajes, viaje_out),
        _write_csv(df_intervalos, intervalos_out),
    )


if __name__ == "__main__":
    import glob, os
    matches = glob.glob(str(RAW_VIAJE_PATH / "RV_*.csv"))
    if not matches:
        raise SystemExit(f"No hay CSV RV_*.csv en {RAW_VIAJE_PATH}")
    latest = Path(max(matches, key=os.path.getmtime))
    print(f"[test manual] procesando: {latest.name}")
    viaje_path, intervalos_path = transform(latest)
    print(f"VIAJE:                     {viaje_path}")
    print(f"INTERVALOS_Y_CUMPLIMIENTOS: {intervalos_path}")