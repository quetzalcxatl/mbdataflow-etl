# -*- coding: utf-8 -*-
'''
Drive Loader — sube los CSVs procesados de CanBus a sus carpetas de Drive.
Cada marca tiene su propio directorio local y su propia carpeta en Drive.
'''

import os
from datetime import datetime
from pathlib import Path
import pytz

from google.oauth2.credentials             import Credentials
from google_auth_oauthlib.flow             import InstalledAppFlow
from google.auth.transport.requests        import Request
from googleapiclient.discovery             import build
from googleapiclient.http                  import MediaFileUpload

from config.settings import (
    PROCESSED_CANBUS_PATH,
    CANDATA_DRIVE_PROCESSED_FOLDERS,
    TOKEN_PATH,
    CREDS_PATH,
)
from utils.logger import ok, info, err, seccion, fmt_fecha

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Mapeo marca → nombre de archivo esperado
MARCAS_ARCHIVOS = {
    'ALEXANDER_DENNIS': lambda f: f"alexander_dennis_{f}.csv",
    'BYD':              lambda f: f"BYD_{f}.csv",
    'MERCEDES_BENZ':    lambda f: f"mercedes_{f}.csv",
    'VOLVO':            lambda f: f"volvo_{f}.csv",
    'YUTONG':           lambda f: f"yutong_{f}.csv",
    'SCANIA':           lambda f: f"scania_{f}.csv",
}


class CanBus_Drive_Loader:
    """
    Sube los CSVs procesados de CanBus a sus carpetas correspondientes en Drive.
    Verifica si el archivo del día existe localmente antes de intentar la subida.
    Si el archivo ya existe en Drive, omite la subida para evitar duplicados.
    """

    def __init__(self):
        self.token_path = TOKEN_PATH
        self.creds_path = CREDS_PATH

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _get_drive_service(self):
        creds = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow  = InstalledAppFlow.from_client_secrets_file(str(self.creds_path), SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "w") as f:
                f.write(creds.to_json())

        return build("drive", "v3", credentials=creds)

    # ------------------------------------------------------------------
    # Verificar duplicado en Drive
    # ------------------------------------------------------------------

    def _file_already_exists(self, service, filename: str, folder_id: str) -> str | None:
        query = (
            f"name = '{filename}' "
            f"and '{folder_id}' in parents "
            f"and mimeType != 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        response = (
            service.files()
            .list(q=query, fields="files(id, name)", spaces="drive")
            .execute()
        )
        files = response.get("files", [])
        return files[0]["id"] if files else None

    # ------------------------------------------------------------------
    # Subida de un archivo
    # ------------------------------------------------------------------

    def _upload_file(self, service, file_path: Path, folder_id: str) -> str:
        filename      = file_path.name
        existing_id   = self._file_already_exists(service, filename, folder_id)

        if existing_id:
            info(f"'{filename}' ya existe en Drive — subida omitida. (ID: {existing_id})")
            return existing_id

        file_metadata = {"name": filename, "parents": [folder_id]}
        media         = MediaFileUpload(str(file_path), mimetype="text/csv", resumable=True)
        uploaded      = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, name")
            .execute()
        )
        ok(f"'{filename}' subido exitosamente. (ID: {uploaded['id']})")
        return uploaded["id"]

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self, fecha=None) -> dict:
        if fecha is None:
            tz    = pytz.timezone('America/Mexico_City')
            fecha = datetime.now(tz)

        fecha_str = fmt_fecha(fecha)
        service   = self._get_drive_service()
        resumen   = {}

        for marca, nombre_fn in MARCAS_ARCHIVOS.items():
            seccion(marca)

            # 1. Verificar que existe el directorio de la marca
            marca_dir = PROCESSED_CANBUS_PATH / marca
            if not marca_dir.exists():
                info(f"Directorio no encontrado, se omite: {marca_dir}")
                resumen[marca] = None
                continue

            # 2. Verificar que existe el CSV del día
            filename   = nombre_fn(fecha_str)
            local_path = marca_dir / filename
            folder_id  = CANDATA_DRIVE_PROCESSED_FOLDERS[marca]

            if not local_path.exists():
                err(f"Archivo no encontrado, se omite: {local_path}")
                resumen[marca] = None
                continue

            # 3. Subir a Drive
            try:
                file_id        = self._upload_file(service, local_path, folder_id)
                resumen[marca] = file_id
            except Exception as e:
                err(f"Error al subir '{filename}': {e}")
                resumen[marca] = None
        return resumen



# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------
if __name__ == '__main__':
    from datetime import datetime
    import pytz

    tz    = pytz.timezone('America/Mexico_City')
    fecha = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)  # ← hoy

    loader  = CanBus_Drive_Loader()
    resumen = loader.run(fecha=fecha)

    print("\n  📋  RESUMEN DE CARGA")
    print(f"  {'─'*45}")
    for marca, fid in resumen.items():
        estado = f"✅  {fid}" if fid else "❌  no subido"
        print(f"  {marca:<22}  {estado}")