'''
Helper: Google Drive loader of the .csv report files
'''

import os
import glob
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class load_to_drive():
    """
    Locates a CAN-{dd/mm/yy}.csv file in a download directory
    and uploads it to a shared Google Drive folder.

    Intended to be imported and used by other scripts:

        from mbdataflow.helpers.google_drive_loader import load_to_drive
        loader = load_to_drive(download_dir="...", drive_folder_id="...")
        file_id = loader.run()
    """

    def __init__(
        self,
        download_dir: str,
        drive_folder_id: str,
        token_path: str = "token.json",
        creds_path: str = "credentials.json",
    ):
        self.download_dir    = download_dir
        self.drive_folder_id = drive_folder_id
        self.token_path      = token_path
        self.creds_path      = creds_path

    def find_csv_file(self) -> str:
        date_str  = datetime.now().strftime("%d/%m/%y")
        filename  = f"CAN-{date_str}.csv"
        full_path = Path(self.download_dir) / filename

        if full_path.exists():
            return str(full_path)

        pattern = str(Path(self.download_dir) / "CAN-*.csv")
        matches = glob.glob(pattern)
        if matches:
            return max(matches, key=os.path.getmtime)

        raise FileNotFoundError(
            f"No CSV file matching '{filename}' found in {self.download_dir}"
        )

    def _get_drive_service(self):
        creds = None

        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.creds_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "w") as f:
                f.write(creds.to_json())

        return build("drive", "v3", credentials=creds)

    def upload_to_drive(self, file_path: str) -> str:
        service  = self._get_drive_service()
        filename = os.path.basename(file_path)

        file_metadata = {
            "name":    filename,
            "parents": [self.drive_folder_id],
        }
        media = MediaFileUpload(file_path, mimetype="text/csv", resumable=True)

        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, name")
            .execute()
        )
        return uploaded["id"]

    def run(self) -> str:
        """Finds the CSV and uploads it. Returns the Drive file ID."""
        csv_path = self.find_csv_file()
        file_id  = self.upload_to_drive(csv_path)
        return file_id


# Optional: keep a smoke-test for running the file directly during development
if __name__ == "__main__":
    import sys

    loader =load_to_drive(
        download_dir=sys.argv[1] if len(sys.argv) > 1 else "/tmp/downloads",
        drive_folder_id=sys.argv[2] if len(sys.argv) > 2 else "1JJjBqTYWyRuDjqZwN_slmLZcMGEI7Dga",
    )
    fid = loader.run()
    print(f"Done — Drive file ID: {fid}")


    '''https://drive.google.com/drive/folders/1JJjBqTYWyRuDjqZwN_slmLZcMGEI7Dga'''