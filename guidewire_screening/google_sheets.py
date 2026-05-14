from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .google_auth import ServiceAccountTokenProvider
from .results import RESULT_COLUMNS, ScreeningResult, screening_result_to_row


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


@dataclass
class GoogleSheetsResultSink:
    spreadsheet_id: str
    token_provider: ServiceAccountTokenProvider
    range_name: str = "Results!A:O"

    def append(self, result: ScreeningResult) -> dict:
        row = screening_result_to_row(result)
        values = [[row[column] for column in RESULT_COLUMNS]]
        params = urllib.parse.urlencode(
            {
                "valueInputOption": "USER_ENTERED",
                "insertDataOption": "INSERT_ROWS",
            }
        )
        url = f"{SHEETS_API}/{self.spreadsheet_id}/values/{urllib.parse.quote(self.range_name, safe='!')}:append?{params}"
        request = urllib.request.Request(
            url,
            data=json.dumps({"values": values}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token_provider.token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def header_values(self) -> list[str]:
        return RESULT_COLUMNS
