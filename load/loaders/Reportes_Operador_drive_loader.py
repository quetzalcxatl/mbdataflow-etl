# load/loaders/Desinc_drive_loader.py
# -*- coding: utf-8 -*-
'''
Helper: Google Drive loader of the Desincorporaciones .csv report files
'''

import os
import glob
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials          import Credentials
from google_auth_oauthlib.flow          import InstalledAppFlow
from google.auth.transport.requests     import Request
from googleapiclient.discovery          import build
from googleapiclient.http               import MediaFileUpload

from config.settings import (
    RAW_REPORTES_OPERADOR_PATH,
    DRIVE_REPORTES_OPERADOR_FOLDER_ID,
    TOKEN_PATH,
    CREDS_PATH,
)
from utils.logger import ok, info, err

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class OpReport_load_to_drive:
    """
    Localiza el archivo Reporte_Operadores-{date_name}.csv en data/raw/downloads_Reporte_Operadores/
    y lo sube a la carpeta de Drive correspondiente.
    Si el archivo ya existe en Drive, omite la subida para evitar duplicados.
    """

    def __init__(self):
        self.download_dir    = RAW_REPORTES_OPERADOR_PATH
        self.drive_folder_id = DRIVE_REPORTES_OPERADOR_FOLDER_ID
        self.token_path      = TOKEN_PATH
        self.creds_path      = CREDS_PATH

    # ------------------------------------------------------------------
    # Búsqueda local del archivo
    # ------------------------------------------------------------------

    def find_csv_file(self) -> Path:
        from datetime import datetime, timedelta

        now = datetime.now()
        # Calcular el último viernes (incluyendo hoy si es viernes)
        # weekday(): lunes=0, martes=1, miércoles=2, jueves=3, viernes=4, sábado=5, domingo=6
        days_since_friday = (now.weekday() - 4) % 7   # 0 si hoy es viernes
        last_friday = now - timedelta(days=days_since_friday)

        # La semana vencida comienza en el viernes anterior al último viernes
        inicio_semana_vencida = last_friday - timedelta(days=7)
        fin_semana_vencida = inicio_semana_vencida + timedelta(days=6)  # jueves

        # Formatear
        fecha_i_ = inicio_semana_vencida.strftime("%d%m%y")
        fecha_f_ = fin_semana_vencida.strftime("%d%m%y")
        fecha_name = f"{fecha_i_}_a_{fecha_f_}"
        
        filename  = f"Reportes_Operador_{fecha_name}.csv"
        full_path = self.download_dir / filename

        if full_path.exists():
            return full_path

        pattern = str(self.download_dir / "Reportes_Operador-*.csv")
        matches = glob.glob(pattern)
        if matches:
            latest = max(matches, key=os.path.getmtime)
            return Path(latest)

        raise FileNotFoundError(
            f"No se encontró '{filename}' en {self.download_dir}"
        )

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

    def _file_already_exists(self, service, filename: str) -> str | None:
        query = (
            f"name = '{filename}' "
            f"and '{self.drive_folder_id}' in parents "
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
    # Subida
    # ------------------------------------------------------------------

    def upload_to_drive(self, file_path: Path) -> str:
        service  = self._get_drive_service()
        filename = file_path.name

        # 1. Verificar duplicado
        existing_id = self._file_already_exists(service, filename)
        if existing_id:
            info(f"'{filename}' ya existe en Drive — subida omitida. (ID: {existing_id})")
            return existing_id

        # 2. Subir
        file_metadata = {"name": filename, "parents": [self.drive_folder_id]}
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

    def run(self) -> str | None:
        """Localiza el CSV del día y lo sube si no existe en Drive. Retorna el file ID."""
        try:
            csv_path = self.find_csv_file()
            file_id  = self.upload_to_drive(csv_path)
            return file_id
        except FileNotFoundError as e:
            err(str(e))
            return None


# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------

if __name__ == "__main__":
    loader  = OpReport_load_to_drive()
    file_id = loader.run()

    print("\n  📋  RESUMEN DE CARGA")
    print(f"  {'─'*45}")
    if file_id:
        print(f"  Reportes a operador   ✅  {file_id}")
    else:
        print(f"  Reportes a operador   ❌  no subido")