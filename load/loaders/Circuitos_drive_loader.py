# load/loaders/Circuitos_drive_loader.py
# -*- coding: utf-8 -*-
'''
Helper: Google Drive loader for Circuitos .csv report files.

Uploads a single CSV (already produced by the scraper) to a specific Drive folder.
Designed to be instantiated twice per pipeline run — once per report type
(desglosado / ejecutivo) — with the corresponding file path and folder ID.
'''

from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http      import MediaFileUpload

from utils.logger import ok, info, err


class Circuitos_load_to_drive:
    """
    Sube un archivo CSV ya descargado a una carpeta específica de Drive.

    A diferencia del loader de Desinc, este recibe el path del archivo y el
    folder destino por constructor — no recalcula fechas ni busca el archivo
    en disco por patrón. El scraper es la única fuente de verdad sobre qué
    archivo subir y dónde.

    Si el archivo ya existe en la carpeta destino, omite la subida.
    """

    def __init__(self, file_path: Path, folder_id: str):
        self.file_path = Path(file_path)
        self.folder_id = folder_id

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _get_drive_service(self):
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
        response = (
            service.files()
            .list(
                q=query,
                fields="files(id, name)",
                spaces="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = response.get("files", [])
        return files[0]["id"] if files else None

    # ------------------------------------------------------------------
    # Subida
    # ------------------------------------------------------------------

    def upload_to_drive(self) -> str:
        service  = self._get_drive_service()
        filename = self.file_path.name

        # 1. Verificar duplicado
        existing_id = self._file_already_exists(service, filename)
        if existing_id:
            info(f"'{filename}' ya existe en Drive — subida omitida. (ID: {existing_id})")
            return existing_id

        # 2. Subir
        file_metadata = {"name": filename, "parents": [self.folder_id]}
        media         = MediaFileUpload(str(self.file_path), mimetype="text/csv", resumable=True)
        uploaded      = (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, name",
                supportsAllDrives=True,
            )
            .execute()
        )
        ok(f"'{filename}' subido exitosamente. (ID: {uploaded['id']})")
        return uploaded["id"]

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self) -> str | None:
        """Sube el archivo al folder configurado. Retorna el file ID o None si falla."""
        try:
            if not self.file_path.exists():
                raise FileNotFoundError(f"No existe el archivo local: {self.file_path}")
            return self.upload_to_drive()
        except FileNotFoundError as e:
            err(str(e))
            return None


# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Para probar manualmente, importa los paths y folder IDs desde settings
    # y crea instancias apuntando a un archivo existente.
    from config.settings import (
        RAW_CIRCUITOS_PATH,
        DRIVE_CIRC_DESGLOSADO_FOLDER_ID,
        DRIVE_CIRC_EJECUTIVO_FOLDER_ID,
    )
    import glob, os

    # Toma el más reciente de cada tipo solo para test manual
    desg_matches = glob.glob(str(RAW_CIRCUITOS_PATH / "Circ_desglosado_*.csv"))
    ejec_matches = glob.glob(str(RAW_CIRCUITOS_PATH / "Circ_ejecutivo_*.csv"))

    results = {}
    if desg_matches:
        path = Path(max(desg_matches, key=os.path.getmtime))
        results["desglosado"] = Circuitos_load_to_drive(path, DRIVE_CIRC_DESGLOSADO_FOLDER_ID).run()
    if ejec_matches:
        path = Path(max(ejec_matches, key=os.path.getmtime))
        results["ejecutivo"] = Circuitos_load_to_drive(path, DRIVE_CIRC_EJECUTIVO_FOLDER_ID).run()

    print("\n  📋  RESUMEN DE CARGA")
    print(f"  {'─'*45}")
    for kind, fid in results.items():
        status = f"✅  {fid}" if fid else "❌  no subido"
        print(f"  Circuitos {kind:<12} {status}")