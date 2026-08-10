# load/loaders/Viaje_drive_loader.py
# -*- coding: utf-8 -*-
'''
Helper: Google Drive loader for the Viaje .csv report file.

Uploads a single CSV (already produced by the scraper) to a specific Drive folder.
A diferencia de Circuitos —que instancia el loader dos veces, una por reporte
(desglosado / ejecutivo)— Viaje produce un único archivo por corrida, así que
este loader se instancia una sola vez con el path y el folder ID destino.

El scraper es la única fuente de verdad sobre qué archivo subir y dónde:
este loader no recalcula fechas ni busca el archivo en disco por patrón.
Si el archivo ya existe en la carpeta destino, omite la subida.

MEDULAR: en el pipeline_Viaje esta carga es parte del camino crítico (no un
respaldo opcional). Los errores propagan sin capturar; el llamador decide qué
hacer con ellos. Ver `pipeline_Viaje._drive_backup`.

Reintentos: se distingue TRANSITORIO (5xx / 429 / red) de PERMANENTE (4xx auth,
permisos, config). Solo lo transitorio se reintenta con backoff exponencial;
lo permanente falla en el primer intento. Ver `_with_retry` abajo.
'''

import time
import warnings
from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors    import HttpError
from googleapiclient.http      import MediaFileUpload

from utils.logger import ok, info, err


# ------------------------------------------------------------------
# Política de reintentos para llamadas al Drive API
# ------------------------------------------------------------------

# HTTP status codes que representan fallos temporales del servidor o rate-limits.
# Google recomienda backoff exponencial para todos ellos.
# Los 4xx que NO están aquí (401, 403, 404, 400) son config incorrecta y no se
# reintentan: reintentar no cambia el desenlace y alarga la agonía.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


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


# ==================================================================
# Loader
# ==================================================================

class Viaje_load_to_drive:
    """
    Sube el CSV de Viaje ya descargado a una carpeta específica de Drive.

    Recibe el path del archivo y el folder destino por constructor —mismo
    contrato que Circuitos_load_to_drive—. Si el archivo ya existe en la
    carpeta destino, omite la subida (idempotente frente a re-ejecuciones).

    Contrato de errores (MEDULAR): cualquier fallo propaga. Un fallo aquí debe
    dejar el Cloud Run Job en FAILED para que la alerta de Cloud Monitoring
    dispare — ese es el contrato explícito con el llamador.
    """

    def __init__(self, file_path: Path, folder_id: str):
        self.file_path = Path(file_path)
        self.folder_id = folder_id

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _get_drive_service(self):
        # Sin retry: un fallo aquí es config (credenciales/scopes), no red.
        SCOPES = ["https://www.googleapis.com/auth/drive.file"]
        creds, _ = google.auth.default(scopes=SCOPES)
        return build("drive", "v3", credentials=creds)

    # ------------------------------------------------------------------
    # Verificar duplicado en Drive
    # ------------------------------------------------------------------

    def _file_already_exists(self, service, filename: str) -> str | None:
        query = (
            f"name = '{filename}' "
            f"and '{self.folder_id}' in parents "
            f"and mimeType != 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        # La llamada de red va envuelta en retry: files.list puede recibir un 5xx
        # transitorio igual que cualquier otra llamada al API.
        response = _with_retry(lambda: (
            service.files()
            .list(
                q=query,
                fields="files(id, name)",
                spaces="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        ))
        files = response.get("files", [])
        return files[0]["id"] if files else None

    # ------------------------------------------------------------------
    # Subida
    # ------------------------------------------------------------------

    def upload_to_drive(self) -> str:
        service  = self._get_drive_service()
        filename = self.file_path.name

        # 1. Verificar duplicado (idempotencia semanal)
        existing_id = self._file_already_exists(service, filename)
        if existing_id:
            info(f"'{filename}' ya existe en Drive — subida omitida. (ID: {existing_id})")
            return existing_id

        # 2. Subir con retry.
        # NOTA: MediaFileUpload(resumable=True) ya reintenta chunks internamente
        # DURANTE la subida en curso, pero un 5xx en el commit final del upload
        # no lo reintenta desde adentro — ese es el hueco que este wrapper tapa.
        file_metadata = {"name": filename, "parents": [self.folder_id]}
        media = MediaFileUpload(str(self.file_path), mimetype="text/csv", resumable=True)

        uploaded = _with_retry(lambda: (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, name",
                supportsAllDrives=True,
            )
            .execute()
        ))
        ok(f"'{filename}' subido exitosamente. (ID: {uploaded['id']})")
        return uploaded["id"]

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self) -> str:
        """
        Sube el archivo al folder configurado. Retorna el file ID.

        MEDULAR: propaga cualquier fallo (FileNotFoundError, HttpError permanente,
        transitorio agotado). NO devuelve None ni captura silenciosamente.
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"No existe el archivo local: {self.file_path}")
        return self.upload_to_drive()


# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Para probar manualmente, importa el path y folder ID desde settings
    # y crea una instancia apuntando a un archivo existente.
    from config.settings import (
        RAW_VIAJE_PATH,
        DRIVE_VIAJE_FOLDER_ID,
    )
    import glob, os

    # Toma el más reciente solo para test manual
    matches = glob.glob(str(RAW_VIAJE_PATH / "RV_*.csv"))

    if not matches:
        print("No se encontró ningún archivo Viaje-*.csv para probar.")
    else:
        path = Path(max(matches, key=os.path.getmtime))
        try:
            file_id = Viaje_load_to_drive(path, DRIVE_VIAJE_FOLDER_ID).run()
            print(f"\n  📋  RESUMEN DE CARGA")
            print(f"  {'─'*45}")
            print(f"  Viaje ✅  {file_id}")
        except Exception as exc:
            print(f"\n  📋  RESUMEN DE CARGA")
            print(f"  {'─'*45}")
            print(f"  Viaje ❌  {type(exc).__name__}: {exc}")
            raise