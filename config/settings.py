"""
Settings for MBDataflow.
Configuration is loaded from environment variables to support running the same code locally
and in Cloud Run without modification.

Local Development:
Create a `.env` file at the project root (gitignored) and set the variables you need. python-dotenv
will load it automatically.

Cloud Run:
Environment variables are injected via the Job configuration, wiith secrets sourced from Secret Manager
"""

import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# Load .env if present (no-op in Cloud Run, ehre there is no .env file)
load_dotenv(find_dotenv())

PROJECT_ROOT = Path(__file__).resolve().parent.parent # Project root

def _runtime_path(local_path: Path) -> Path:
    """Resolve a path based on runtime environment.

    Returns /tmp when running in Cloud Run (the only writable location in
    the container), otherwise returns the local development path.
    """
    is_cloud_run = any(
        k in os.environ for k in ("CLOUD_RUN_JOB", "K_SERVICE", "CLOUD_RUN_EXECUTION")
    )
    return Path("/tmp") if is_cloud_run else local_path


# --- GCP ---------------------------------------------------------
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

# Local only: path to the Service Account JSON file key file.
# In Cloud Run, ADC uses the Job's attached SA - this variable is irrelevant.
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

# --- BigQuery ---------------------------------------------------
DATASET_ID        = os.environ.get("BQ_DATASET_ID")
SONDA_PV_TABLE_ID = os.environ.get("BQ_SONDA_PV_TABLE_ID")  

BQ_PROJECT             = os.environ.get("BQ_PROJECT")
BQ_DATASET_SONDA       = os.environ.get("BQ_DATASET_SONDA", "Sonda")
BQ_DATASET_INTERTRAMOS = os.environ.get("BQ_DATASET_INTERTRAMOS", "TIEMPO_INTERTRAMOS")

BQ_TABLE_VIAJES       = f"{BQ_PROJECT}.{BQ_DATASET_SONDA}.VIAJES_smoketest"
BQ_TABLE_INTERVALOS   = f"{BQ_PROJECT}.{BQ_DATASET_SONDA}.INTERVALOS_smoketest"
BQ_TABLE_INT_CUMPL    = f"{BQ_PROJECT}.{BQ_DATASET_INTERTRAMOS}.INTERVALOS_Y_CUMPLIMIENTOS_smoketest"

# --- Sonda Platform Credentials ---------------------------------
SONDA_QUERY_USER        = os.environ.get("SONDA_QUERY_USER")
SONDA_QUERY_PASSWORD    = os.environ.get("SONDA_QUERY_PASSWORD")
SONDA_PERSONAL_USER     = os.environ.get("SONDA_PERSONAL_USER")
SONDA_PERSONAL_PASSWORD = os.environ.get("SONDA_PERSONAL_PASSWORD")

# --- Drive Folder IDs (per pipeline) ----------------------------
DRIVE_DESINC_FOLDER_ID            = os.environ.get("DRIVE_DESINC_FOLDER_ID")
DRIVE_REPORTES_OPERADOR_FOLDER_ID = os.environ.get("DRIVE_REPORTES_OPERADOR_FOLDER_ID")
DRIVE_CIRC_DESGLOSADO_FOLDER_ID   = os.environ.get("DRIVE_CIRC_DESGLOSADO_FOLDER_ID")
DRIVE_CIRC_EJECUTIVO_FOLDER_ID    = os.environ.get("DRIVE_CIRC_EJECUTIVO_FOLDER_ID")
DRIVE_VIAJE_FOLDER_ID                = os.environ.get("DRIVE_RV_FOLDER_ID")

# --- CanBus multiple folders ------------------------------------
CANDATA_DRIVE_PROCESSED_FOLDERS = {
    "ALEXANDER_DENNIS": os.environ.get("DRIVE_CANBUS_ALEXANDER_DENNIS", ""),
    "BYD":              os.environ.get("DRIVE_CANBUS_BYD", ""),
    "MERCEDES_BENZ":    os.environ.get("DRIVE_CANBUS_MERCEDES_BENZ", ""),
    "VOLVO":            os.environ.get("DRIVE_CANBUS_VOLVO", ""),
    "YUTONG":           os.environ.get("DRIVE_CANBUS_YUTONG", ""),
    "SCANIA":           os.environ.get("DRIVE_CANBUS_SCANIA", ""),
}

# --- Local filesystem paths (derived from project root) ----------
# Cloud Run override happens at the scraper level (PR 1.3)
RAW_DESINC_PATH            = _runtime_path(PROJECT_ROOT / "data" / "raw" / "downloads_Desincorporaciones")
RAW_CANBUS_PATH            = PROJECT_ROOT / "data" / "raw" / "downloads_CanBus"
PROCESSED_CANBUS_PATH      = PROJECT_ROOT / "data" / "processed" / "processed_CanBus"
RAW_REPORTES_OPERADOR_PATH = PROJECT_ROOT / "data" / "raw" / "downloads_Reporte_Operadores"
RAW_CIRCUITOS_PATH         = _runtime_path(PROJECT_ROOT / "data" / "raw" / "downloads_Circuitos")
RAW_FLOTAV_PATH            = PROJECT_ROOT / "data" / "raw" / "downloads_Flota_Vehicular"
PROCESSED_FLOTAV_PATH      = PROJECT_ROOT / "data" / "processed" / "processed_Flota_Vehicular"
RAW_VIAJE_PATH             = _runtime_path(PROJECT_ROOT / "data" / "raw" / "downloads_Viaje")
PROCESSED_VIAJE_PATH       = _runtime_path(PROJECT_ROOT / "data" / "processed" / "processed_Viaje")
                                           
# --- SQL remote transform layers ----------------------------------------
# In this section we add the location of SQL queries that runs over remote warehouse
SQL_INTERVALOSDINAMICOS_PATH = _runtime_path(PROJECT_ROOT / "transform" / "sql" / "intervalos_dinamicos.sql")


# Static configuration (not environment-dependent) ---------------
MARCAS_CONFIG = [
    {
        'nombre'    : 'Volvo',
        'prefix'    : 'Volvo',
        'borrar'    : ['Empresa', 'Marca', 'Luz Check Engine', 'Luz Precaucion',
                       'Luz Alerta', 'Switch Acelerador', 'Torque Actual (%)',
                       'Torque Solicitado (%)', 'Temperatura Exterior (°C)'],
        'output_fn' : lambda f: PROCESSED_CANBUS_PATH / "VOLVO"            / f"volvo_{f}.csv",
    },
    {
        'nombre'    : 'Yutong',
        'prefix'    : 'Yutong',
        'borrar'    : ['Empresa', 'Marca', 'Switch Acelerador',
                       'Torque Actual (%)', 'Torque Solicitado (%)'],
        'output_fn' : lambda f: PROCESSED_CANBUS_PATH / "YUTONG"           / f"yutong_{f}.csv",
    },
    {
        'nombre'    : 'BYD',
        'prefix'    : 'BYD',
        'borrar'    : ['Empresa', 'Marca'],
        'output_fn' : lambda f: PROCESSED_CANBUS_PATH / "BYD"              / f"BYD_{f}.csv",
    },
    {
        'nombre'    : 'Mercedes Benz',
        'prefix'    : 'Merc',
        'borrar'    : ['Empresa', 'Marca'],
        'output_fn' : lambda f: PROCESSED_CANBUS_PATH / "MERCEDES_BENZ"    / f"mercedes_{f}.csv",
    },
    {
        'nombre'    : 'Alexander Dennis',
        'prefix'    : 'ADennis',
        'borrar'    : ['Empresa', 'Marca'],
        'output_fn' : lambda f: PROCESSED_CANBUS_PATH / "ALEXANDER_DENNIS" / f"alexander_dennis_{f}.csv",
    },
    {
        'nombre'    : 'Scania',
        'prefix'    : 'Scania',
        'borrar'    : ['Empresa', 'Marca'],
        'output_fn' : lambda f: PROCESSED_CANBUS_PATH / "SCANIA"           / f"scania_{f}.csv",
    },
]