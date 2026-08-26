# load/loaders/Pasos_drive_loader.py
# -*- coding: utf-8 -*-
'''
Helper: Google Drive loader para los CSV del reporte de Pasos por parada.

A diferencia de Viaje —que sube un único archivo a un folder ID fijo— Pasos
produce 10 CSV por corrida (uno por línea operativa) que aterrizan todos en la
MISMA carpeta de destino, resuelta en runtime siguiendo la jerarquía:

    reporte_de_pasos/          <- root, ID fijo por env var
    └── <año>/                 <- p.ej. '2026'
        └── <semana>/          <- p.ej. '01'
            ├── pasos_L1_sem01_...csv
            └── ... (10 archivos)

El año y la semana llegan por CONSTRUCTOR desde el orquestador, que ya los
conoce vía utils.dates. Este loader NO los deriva parseando nombres de archivo:
la fuente de verdad es el argumento, no la convención de naming.

MEDULAR: igual que Viaje, la carga es camino crítico. Ver `run()` para la
política de errores (best-effort con raise final) — se implementa en PR-B.

ESTADO: PR-A. Solo resuelve/crea la jerarquía de carpetas. La subida de
archivos llega en PR-B.

DEUDA CONOCIDA: `_is_transient` / `_with_retry` están duplicados desde
`Viaje_drive_loader.py`. Extraer a un módulo compartido es un refactor
deliberadamente fuera del scope de este PR.
'''

import time
import warnings
from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors    import HttpError

from utils.logger import ok, info


# ------------------------------------------------------------------
# Constantes de módulo
# ------------------------------------------------------------------

# Scope COMPLETO, no 'drive.file'.
#
# 'drive.file' limita el acceso a archivos creados por la propia app. Este
# loader necesita localizar carpetas creadas manualmente por un humano en la
# unidad compartida (la raíz 'reporte_de_pasos' y, si ya existen, las de año y
# semana). Con 'drive.file' esas carpetas serían invisibles y el loader crearía
# duplicados o fallaría con 404 al intentar escribir bajo un parent que no ve.
#
# Si el test empírico demuestra que 'drive.file' basta en este setup, bajar el
# scope aquí —es una sola línea— y documentarlo.
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_MIME = "application/vnd.google-apps.folder"

# HTTP status codes que representan fallos temporales del servidor o rate-limits.
# Los 4xx que NO están aquí (401, 403, 404, 400) son config incorrecta y no se
# reintentan: reintentar no cambia el desenlace.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


# ------------------------------------------------------------------
# Convención de nombres de carpeta
# ------------------------------------------------------------------
#
# CRÍTICO: estos nombres deben coincidir EXACTAMENTE con los de las carpetas ya
# existentes en Drive. Drive permite hermanos con nombre duplicado, así que un
# desajuste ('01' vs '1') no lanza error — crea un árbol paralelo en silencio.

def _year_folder_name(year: int) -> str:
    """Nombre de la carpeta de año. Convención: '2026', '2027', ..."""
    return f"{year:04d}"


def _week_folder_name(week_number: int) -> str:
    """Nombre de la carpeta de semana. Convención: '01', '02', ..., '53'.

    Si las carpetas existentes en Drive resultan estar SIN padding ('1', '2'),
    cambiar a `str(week_number)` — y solo aquí.
    """
    return f"{week_number:02d}"


# ------------------------------------------------------------------
# Política de reintentos para llamadas al Drive API
# (duplicado de Viaje_drive_loader — ver DEUDA CONOCIDA arriba)
# ------------------------------------------------------------------

def _is_transient(exc: Exception) -> bool:
    """True si la excepción representa un fallo transitorio que vale reintentar."""
    if isinstance(exc, HttpError):
        return exc.resp.status in _TRANSIENT_STATUS
    # Errores a nivel red: la petición nunca llegó al servidor o la respuesta
    # nunca volvió. Siempre transitorios.
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _with_retry(fn, *, attempts: int = 3, base_delay: float = 2.0):
    """
    Ejecuta `fn()` reintentando SOLO errores transitorios con backoff exponencial
    (2s, 4s, 8s por default).

    - Errores permanentes (4xx que no sean 429): propagan en el primer intento.
    - Errores transitorios: hasta `attempts` intentos; si todos fallan, propaga
      el último.

    No se captura BaseException; solo Exception. Ctrl+C sigue interrumpiendo.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not _is_transient(exc):
                raise  # permanente → propaga sin reintentar
            if attempt == attempts:
                raise  # agotamos los reintentos
            delay = base_delay * (2 ** (attempt - 1))
            warnings.warn(
                f"Drive: intento {attempt}/{attempts} falló "
                f"({type(exc).__name__}); reintento en {delay:.0f}s",
                RuntimeWarning,
            )
            time.sleep(delay)


def _escape_q(value: str) -> str:
    """Escapa un literal para interpolarlo en una query de Drive API.

    Los nombres que maneja este loader son numéricos, así que hoy es defensa
    redundante. Se deja porque el costo es cero y el día que alguien pase un
    nombre con apóstrofo la query se rompería de forma opaca.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


# ==================================================================
# Loader
# ==================================================================

class Pasos_load_to_drive:
    """
    Sube los CSV de Pasos ya descargados a la jerarquía año/semana en Drive.

    Contrato de construcción:
      file_paths     — los Path que retornó `Pasos_Scraper.scrape()`.
      root_folder_id — ID de la carpeta 'reporte_de_pasos' en la unidad
                       compartida. Debe existir; este loader NO la crea (crear
                       la raíz enmascararía un folder ID mal configurado).
      year           — año calendario de la semana operativa.
      week_number    — número de semana operativa (1..53).
    """

    def __init__(
        self,
        file_paths: list[Path],
        root_folder_id: str,
        year: int,
        week_number: int,
    ):
        if not root_folder_id:
            raise ValueError("root_folder_id vacío — revisa DRIVE_PASOS_FOLDER_ID")
        if not 1 <= week_number <= 53:
            raise ValueError(f"week_number fuera de rango [1,53]: {week_number}")

        self.file_paths = [Path(p) for p in file_paths]
        self.root_folder_id = root_folder_id
        self.year = int(year)
        self.week_number = int(week_number)

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _get_drive_service(self):
        # Sin retry: un fallo aquí es config (credenciales/scopes), no red.
        creds, _ = google.auth.default(scopes=DRIVE_SCOPES)
        return build("drive", "v3", credentials=creds)

    # ------------------------------------------------------------------
    # Resolución idempotente de carpetas
    # ------------------------------------------------------------------

    def _find_folder(self, service, name: str, parent_id: str) -> str | None:
        """Devuelve el ID de la subcarpeta `name` bajo `parent_id`, o None.

        Si hay más de un match (hermanos con nombre duplicado, que Drive
        permite), devuelve el primero y avisa: es un síntoma de que alguien
        creó una carpeta a mano con la misma etiqueta, o de que la convención
        de nombres cambió a mitad de camino.
        """
        query = (
            f"name = '{_escape_q(name)}' "
            f"and '{parent_id}' in parents "
            f"and mimeType = '{FOLDER_MIME}' "
            f"and trashed = false"
        )
        response = _with_retry(lambda: (
            service.files()
            .list(
                q=query,
                fields="files(id, name)",
                spaces="drive",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        ))
        folders = response.get("files", [])
        if not folders:
            return None
        if len(folders) > 1:
            warnings.warn(
                f"Drive: {len(folders)} carpetas llamadas '{name}' bajo "
                f"{parent_id}. Se usa la primera ({folders[0]['id']}). "
                f"Revisa duplicados en la unidad compartida.",
                RuntimeWarning,
            )
        return folders[0]["id"]

    def _create_folder(self, service, name: str, parent_id: str) -> str:
        """Crea la subcarpeta `name` bajo `parent_id` y devuelve su ID."""
        metadata = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }
        created = _with_retry(lambda: (
            service.files()
            .create(
                body=metadata,
                fields="id, name",
                supportsAllDrives=True,
            )
            .execute()
        ))
        return created["id"]

    def _get_or_create_folder(self, service, name: str, parent_id: str) -> str:
        """Idempotente: devuelve el ID de `name` bajo `parent_id`, creándola
        si hace falta.

        NO es atómico. Dos corridas concurrentes podrían crear dos carpetas
        homónimas. Aceptable: el Job es único y disparado por Cloud Scheduler
        una vez por semana. Si algún día hay concurrencia real, esto necesita
        un re-check post-create o un lock externo.
        """
        existing = self._find_folder(service, name, parent_id)
        if existing:
            info(f"Carpeta '{name}' ya existe (ID: {existing})")
            return existing

        created_id = self._create_folder(service, name, parent_id)
        ok(f"Carpeta '{name}' creada (ID: {created_id})")
        return created_id

    def _resolve_week_folder(self, service) -> str:
        """Encadena root → año → semana. Devuelve el ID del folder de semana,
        que es donde aterrizan los 10 CSV.
        """
        year_name = _year_folder_name(self.year)
        week_name = _week_folder_name(self.week_number)

        year_id = self._get_or_create_folder(service, year_name, self.root_folder_id)
        week_id = self._get_or_create_folder(service, week_name, year_id)

        info(f"Destino resuelto: {year_name}/{week_name} (ID: {week_id})")
        return week_id

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self) -> str:
        """
        PR-A: resuelve (creando si hace falta) la carpeta año/semana destino y
        devuelve su ID. NO sube archivos todavía — eso llega en PR-B.

        MEDULAR: propaga cualquier fallo. NO devuelve None ni captura
        silenciosamente.
        """
        service = self._get_drive_service()
        return self._resolve_week_folder(service)


# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------
#
# python -m load.loaders.Pasos_drive_loader
#
# Qué verificar en la UI de Drive después de correrlo:
#   1. Que la carpeta de año existente NO se haya duplicado.
#   2. Que la carpeta de semana se cree con el nombre esperado ('01' vs '1').
#   3. Que una SEGUNDA corrida imprima "ya existe" en ambos niveles y devuelva
#      exactamente el mismo week_folder_id.

if __name__ == "__main__":
    from config.settings import DRIVE_PASOS_FOLDER_ID
    from utils.dates import (
        last_completed_operational_week_cdmx,
        last_operational_week_number,
    )

    start, _end = last_completed_operational_week_cdmx()
    week_numero = last_operational_week_number()

    print(f"Semana operativa: año={start.year} semana={week_numero}")
    print(f"Carpetas objetivo: "
          f"{_year_folder_name(start.year)}/{_week_folder_name(week_numero)}")

    loader = Pasos_load_to_drive(
        file_paths=[],              # PR-A no los usa
        root_folder_id=DRIVE_PASOS_FOLDER_ID,
        year=start.year,
        week_number=week_numero,
    )
    week_folder_id = loader.run()

    print(f"\n  📁  RESOLUCIÓN DE DESTINO")
    print(f"  {'─'*45}")
    print(f"  week_folder_id: {week_folder_id}")