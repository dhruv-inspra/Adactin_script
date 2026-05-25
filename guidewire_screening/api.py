from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .google_auth import token_provider_from_env
from .google_sheets import SHEETS_SCOPE, GoogleSheetsResultSink
from .pipeline import build_call_inputs, load_local_role_folder
from .qualification import evaluate_candidate
from .results import append_result_csv, screening_result_to_row
from .vapi import build_vapi_call_payload, place_vapi_call
from .webhook import result_from_vapi_event


def preview_request(request_body: dict[str, Any]) -> dict[str, Any]:
    jd, questions, candidates = _load_source_from_request(request_body)
    candidates = _filter_candidates(candidates, request_body)
    call_inputs = build_call_inputs(candidates, jd, questions)
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    return {
        "role_title": jd.title,
        "question_count": len(questions),
        "candidate_count": len(candidates),
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "name": item.name_if_known,
                "phone": item.phone,
                "question_count": len(item.questions),
                "cv_summary": item.cv_summary,
                "parsed_cv_facts": candidates_by_id[item.candidate_id].screening_facts,
                "cv_predicted_disposition": evaluate_candidate(
                    questions, candidates_by_id[item.candidate_id].screening_facts
                ).disposition,
            }
            for item in call_inputs
        ],
    }


def start_calls_request(request_body: dict[str, Any]) -> dict[str, Any]:
    phone_number_id = request_body.get("phone_number_id") or os.environ.get("VAPI_PHONE_NUMBER_ID")
    if not phone_number_id:
        raise ValueError("phone_number_id or VAPI_PHONE_NUMBER_ID is required")

    jd, questions, candidates = _load_source_from_request(request_body)
    candidates = _filter_candidates(candidates, request_body)
    call_inputs = build_call_inputs(candidates, jd, questions)
    limit = request_body.get("limit")
    if limit:
        call_inputs = call_inputs[: int(limit)]

    server_url = request_body.get("server_url") or os.environ.get("VAPI_SERVER_URL")
    assistant_id = request_body.get("assistant_id") or os.environ.get("VAPI_ASSISTANT_ID")
    payloads = [
        build_vapi_call_payload(
            item,
            phone_number_id=phone_number_id,
            server_url=server_url,
            assistant_id=assistant_id,
        )
        for item in call_inputs
    ]
    if not request_body.get("execute", False):
        return {"status": "payloads_built", "payload_count": len(payloads), "payloads": payloads}

    api_key = request_body.get("api_key") or os.environ.get("VAPI_API_KEY")
    if not api_key:
        raise ValueError("api_key or VAPI_API_KEY is required when execute is true")
    responses = [place_vapi_call(payload, api_key=api_key) for payload in payloads]
    return {"status": "calls_started", "payload_count": len(payloads), "responses": responses}


def process_vapi_event_request(request_body: dict[str, Any]) -> dict[str, Any]:
    event = request_body.get("event") if isinstance(request_body.get("event"), dict) else request_body
    _jd, questions, candidates = _load_source_from_request(request_body)
    result = result_from_vapi_event(
        event,
        candidates_by_id={candidate.candidate_id: candidate for candidate in candidates},
        questions=questions,
    )
    results_csv = request_body.get("results_csv") or os.environ.get("SCREENING_RESULTS_CSV")
    if results_csv:
        append_result_csv(result, Path(results_csv))

    spreadsheet_id = request_body.get("google_spreadsheet_id") or os.environ.get("GOOGLE_SPREADSHEET_ID")
    if spreadsheet_id:
        sink = GoogleSheetsResultSink(
            spreadsheet_id=spreadsheet_id,
            token_provider=token_provider_from_env([SHEETS_SCOPE]),
        )
        sink.append(result)

    return {
        "candidate_id": result.candidate.candidate_id,
        "call_id": result.call_id,
        "disposition": result.disposition,
        "call_outcome": result.call_outcome,
        "failure_reasons": result.failure_reasons,
        "row": screening_result_to_row(result),
    }


def serve_api(host: str = "0.0.0.0", port: int = 4242) -> None:
    server = ThreadingHTTPServer((host, port), ScreeningApiHandler)
    print(f"Guidewire screening API listening on http://{host}:{port}")
    server.serve_forever()


class ScreeningApiHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"ok": True})
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        try:
            body = self._read_json_body()
            if self.path == "/preview":
                self._send_json(preview_request(body))
                return
            if self.path == "/start-calls":
                self._send_json(start_calls_request(body))
                return
            if self.path == "/vapi/end-of-call":
                self._send_json(process_vapi_event_request(body))
                return
            self.send_error(404, "Not found")
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _load_source_from_request(request_body: dict[str, Any]):
    local_folder = (
        request_body.get("local_folder")
        or (request_body.get("source") or {}).get("local_folder")
        or os.environ.get("SCREENING_LOCAL_FOLDER")
        or "."
    )
    if request_body.get("drive_folder_id") or (request_body.get("source") or {}).get("drive_folder_id"):
        from .google_drive import DRIVE_SCOPE, GoogleDriveRoleFolder
        from .parsers import parse_candidates_from_folder, parse_jd, parse_questionnaire
        from .pipeline import build_default_qualification_questions, find_role_files

        drive_folder_id = request_body.get("drive_folder_id") or (request_body.get("source") or {}).get("drive_folder_id")
        provider = token_provider_from_env([DRIVE_SCOPE])
        folder = GoogleDriveRoleFolder(provider).download_folder(
            drive_folder_id,
            Path("outputs") / "drive_cache" / drive_folder_id,
        )
        jd_path, questionnaire_path = find_role_files(folder)
        from .role_questions import build_role_questions

        jd = parse_jd(jd_path)
        questions = (
            parse_questionnaire(questionnaire_path)
            if questionnaire_path
            else build_default_qualification_questions()
        )
        return jd, questions + build_role_questions(jd), parse_candidates_from_folder(folder)

    return load_local_role_folder(Path(local_folder))


def _filter_candidates(candidates: list, request_body: dict[str, Any]) -> list:
    candidate_ids = _as_set(request_body.get("candidate_ids") or request_body.get("candidate_id"))
    candidate_file_ids = _as_set(request_body.get("candidate_file_ids") or request_body.get("candidate_file_id"))
    candidate_file_names = _as_set(request_body.get("candidate_file_names") or request_body.get("candidate_file_name"))
    if not candidate_ids and not candidate_file_ids and not candidate_file_names:
        return candidates

    filtered = []
    for candidate in candidates:
        if candidate_ids and candidate.candidate_id in candidate_ids:
            filtered.append(candidate)
            continue
        if candidate_file_ids and candidate.drive_file_id in candidate_file_ids:
            filtered.append(candidate)
            continue
        if candidate_file_names and candidate.source_file.name in candidate_file_names:
            filtered.append(candidate)
    return filtered


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(item) for item in value if item}
    return {str(value)}
