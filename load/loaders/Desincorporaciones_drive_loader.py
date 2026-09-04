# load/loaders/Desincorporaciones_drive_loader.py
# -*- coding: utf-8 -*-
'''
Helper: Google Drive loader para el CSV del reporte de Desincorporaciones.

Contrato de construcción (alineado con Viaje_load_to_drive): recibe el path
del archivo y el folder ID destino. El scraper es la única fuente de verdad
sobre qué archivo subir; este loader NO re-deriva la fecha ni busca por patrón.

Qué desaparece de la versión anterior y por qué:
  * find_csv_file() — llamaba yesterday_cdmx() por su cuenta, una SEGUNDA
    lectura del reloj independiente de la del scraper. Misma clase de bug que
    documentamos en pipeline_Pasos._iso_destino.
  * Su fallback glob('Desinc-*.csv') — nunca matcheó nada: el scraper escribe
    'Desinc_{fecha}.csv' con guion BAJO. Código muerto que aparentaba ser red
    de seguridad.
  * run() devolviendo None en FileNotFoundError — convertía un fallo de
    extract en un fallo de load reportado 20 líneas después.

Parametrizado por folder_id porque PR-4 introduce un segundo servicio
('Apoyo') que aterriza en otra carpeta con el mismo mecanismo.

SCOPE: drive.file, no auth/drive. Este loader no descubre carpetas —recibe un
ID fijo—, así que no necesita el scope amplio que sí requiere Pasos, que
localiza carpetas creadas a mano.

MEDULAR: cualquier fallo propaga. El Job queda FAILED y dispara la alerta de
Cloud Monitoring.
'''

from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http      import MediaFileUpload

from load.loaders._drive_retry import _with_retry
from utils.logger import ok, info


class Desinc_load_to_drive:
    """Sube un CSV ya descargado a una carpeta específica de Drive."""

    def __init__(self, file_path: Path, folder_id: str):
        if not folder_id:
            raise ValueError("folder_id vacío — revisa la env var de destino")
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

        existing_id = self._file_already_exists(service, filename)
        if existing_id:
            info(f"'{filename}' ya existe en Drive — subida omitida. "
                 f"(ID: {existing_id})")
            return existing_id

        # NOTA: MediaFileUpload(resumable=True) ya reintenta chunks internamente
        # DURANTE la subida en curso, pero un 5xx en el commit final del upload
        # no lo reintenta desde adentro — ese es el hueco que _with_retry tapa.
        file_metadata = {"name": filename, "parents": [self.folder_id]}
        media = MediaFileUpload(str(self.file_path), mimetype="text/csv",
                                resumable=True)

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

        MEDULAR: propaga cualquier fallo (FileNotFoundError, HttpError
        permanente, transitorio agotado). NO devuelve None.
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"No existe el archivo local: {self.file_path}")
        return self.upload_to_drive()


# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------
#
# python -m load.loaders.Desincorporaciones_drive_loader
#
# Toma el CSV más reciente del directorio raw. Correr DOS veces:
#   1ra — 'subido exitosamente'.
#   2da — 'ya existe en Drive', mismo ID, exit code 0.

if __name__ == "__main__":
    import glob, os
    from config.settings import RAW_DESINC_PATH, DRIVE_DESINC_FOLDER_ID

    matches = glob.glob(str(RAW_DESINC_PATH / "Desinc_*.csv"))
    if not matches:
        print(f"No se encontró ningún 'Desinc_*.csv' en {RAW_DESINC_PATH}.")
        raise SystemExit(1)

    path = Path(max(matches, key=os.path.getmtime))
    print(f"Archivo: {path.name}")

    file_id = Desinc_load_to_drive(path, DRIVE_DESINC_FOLDER_ID).run()
    print("\n  📋  RESUMEN DE CARGA")
    print(f"  {'─'*45}")
    print(f"  Desincorporaciones   ✅  {file_id}")