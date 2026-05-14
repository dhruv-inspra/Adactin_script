from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .google_auth import ServiceAccountTokenProvider


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_API = "https://www.googleapis.com/drive/v3"


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str


class GoogleDriveRoleFolder:
    def __init__(self, token_provider: ServiceAccountTokenProvider):
        self.token_provider = token_provider

    def list_files(self, folder_id: str) -> list[DriveFile]:
        query = f"'{folder_id}' in parents and trashed = false"
        params = urllib.parse.urlencode(
            {
                "q": query,
                "fields": "files(id,name,mimeType)",
                "pageSize": "100",
            }
        )
        payload = self._get_json(f"{DRIVE_API}/files?{params}")
        return [
            DriveFile(id=item["id"], name=item["name"], mime_type=item["mimeType"])
            for item in payload.get("files", [])
        ]

    def download_folder(self, folder_id: str, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, dict[str, str]] = {}
        for drive_file in self.list_files(folder_id):
            if _is_supported_role_file(drive_file):
                destination_name = _destination_name(drive_file)
                self.download_file(drive_file, destination / destination_name)
                manifest[destination_name] = {"id": drive_file.id, "mime_type": drive_file.mime_type}
        (destination / ".drive_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return destination

    def download_file(self, drive_file: DriveFile, destination: Path) -> None:
        if drive_file.mime_type.startswith("application/vnd.google-apps."):
            export_mime = _export_mime_type(drive_file)
            url = f"{DRIVE_API}/files/{drive_file.id}/export?{urllib.parse.urlencode({'mimeType': export_mime})}"
        else:
            url = f"{DRIVE_API}/files/{drive_file.id}?alt=media"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(request, timeout=60) as response:
            destination.write_bytes(response.read())

    def _get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token_provider.token()}"}


def _is_supported_role_file(drive_file: DriveFile) -> bool:
    name = drive_file.name.lower()
    if name.endswith((".docx", ".pdf", ".xlsx")):
        return True
    return drive_file.mime_type in {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
    }


def _export_mime_type(drive_file: DriveFile) -> str:
    if drive_file.mime_type == "application/vnd.google-apps.document":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if drive_file.mime_type == "application/vnd.google-apps.spreadsheet":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def _destination_name(drive_file: DriveFile) -> str:
    name = drive_file.name
    lower = name.lower()
    if drive_file.mime_type == "application/vnd.google-apps.document" and not lower.endswith(".docx"):
        return f"{name}.docx"
    if drive_file.mime_type == "application/vnd.google-apps.spreadsheet" and not lower.endswith(".xlsx"):
        return f"{name}.xlsx"
    return name
