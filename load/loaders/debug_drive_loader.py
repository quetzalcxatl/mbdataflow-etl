# load/loaders/debug_drive_loader.py
# -*- coding: utf-8 -*-
"""
Best-effort uploader de artefactos de debug (screenshots, page_source) a Drive.

Uso EXCLUSIVO de observabilidad. Cuando el scraper falla en Cloud Run, sus
screenshots y HTML viven en /tmp y mueren con el contenedor. Este helper los
exfiltra a una carpeta de Drive para poder inspeccionarlos post-mortem.

NO es medular. A diferencia de Viaje_drive_loader (camino crítico, propaga
errores para tumbar el Job y disparar la alerta), aquí el contrato es el
opuesto: best-effort y silencioso por archivo. Exfiltrar evidencia jamás debe
enmascarar la excepción real que la generó; por eso el fallo de una subida se
loguea y se sigue con las demás, y nunca se relanza.

Scope de Drive: 'drive.file' — el mismo que Viaje_drive_loader. Alcanza para
crear archivos nuevos en una carpeta compartida con la Service Account como
Editor. Si se usa DRIVE_VIAJE_DEBUG_FOLDER_ID (carpeta distinta a la del CSV),
esa carpeta también debe estar compartida con la SA.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def _drive_service():
    scopes = ["https://www.googleapis.com/auth/drive.file"]
    creds, _ = google.auth.default(scopes=scopes)
    return build("drive", "v3", credentials=creds)


def upload_debug_files(paths, folder_id: str, *, name_prefix: str = "") -> list[str]:
    """Sube cada archivo de `paths` a `folder_id`.

    Best-effort por archivo: si una subida falla, loguea y continúa con las
    demás. Retorna los IDs subidos con éxito. No lanza salvo un fallo total de
    autenticación al construir el servicio (que el llamador ya envuelve en
    try/except).

    name_prefix: se antepone al nombre de cada archivo para agrupar los
    artefactos de una misma corrida en la carpeta (evita crear subcarpetas,
    que costarían llamadas extra al API).
    """
    service = _drive_service()
    uploaded: list[str] = []

    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            continue
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        name = f"{name_prefix}{p.name}" if name_prefix else p.name
        try:
            media = MediaFileUpload(str(p), mimetype=mime, resumable=False)
            resp = (
                service.files()
                .create(
                    body={"name": name, "parents": [folder_id]},
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
            uploaded.append(resp["id"])
        except Exception as e:  # best-effort: un archivo caído no aborta el resto
            print(f"[DEBUG] fallo subiendo {p.name}: {type(e).__name__}: {e}")

    return uploaded