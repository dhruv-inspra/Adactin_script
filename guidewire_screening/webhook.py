from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .google_auth import token_provider_from_env
from .google_sheets import SHEETS_SCOPE, GoogleSheetsResultSink
from .models import Candidate, Question
from .qualification import evaluate_candidate
from .results import ScreeningResult, append_result_csv, extract_structured_output


SPECIAL_DISPOSITIONS = {
    "no_answer",
    "busy_callback",
    "wrong_number",
    "dnc",
    "consent_declined",
    "incomplete",
}


def result_from_vapi_event(
    event: dict,
    candidates_by_id: dict[str, Candidate],
    questions: list[Question],
) -> ScreeningResult:
    call = event.get("call", event)
    metadata = call.get("metadata") or event.get("metadata") or {}
    candidate_id = metadata.get("candidate_id")
    if not candidate_id or candidate_id not in candidates_by_id:
        raise ValueError(f"Unknown candidate_id in Vapi event: {candidate_id!r}")

    candidate = candidates_by_id[candidate_id]
    structured = extract_structured_output(event)
    answers = _string_map(structured.get("answers") or {})
    signals = structured.get("disposition_signals") or {}
    call_outcome = _derive_call_outcome(call, structured, signals)
    summary = structured.get("summary") or (call.get("analysis") or {}).get("summary") or ""

    if call_outcome in SPECIAL_DISPOSITIONS and call_outcome != "completed":
        disposition = call_outcome
        normalized_answers: dict[str, str] = {}
        per_question_pass: dict[str, bool] = {}
        failure_reasons = [_special_reason(call_outcome)]
    else:
        qualification = evaluate_candidate(questions, answers)
        disposition = qualification.disposition
        normalized_answers = qualification.normalized_answers
        per_question_pass = qualification.per_question_pass
        failure_reasons = qualification.failure_reasons

    return ScreeningResult(
        candidate=candidate,
        call_id=str(call.get("id") or event.get("id") or ""),
        disposition=disposition,
        call_outcome=call_outcome,
        summary=summary,
        answers=answers,
        normalized_answers=normalized_answers,
        per_question_pass=per_question_pass,
        failure_reasons=failure_reasons,
        transcript_url=_transcript_url(call),
    )


def serve_webhook(
    host: str,
    port: int,
    candidates_by_id: dict[str, Candidate],
    questions: list[Question],
    results_csv: Path,
    google_spreadsheet_id: str | None = None,
) -> None:
    sheet_sink = None
    if google_spreadsheet_id:
        sheet_sink = GoogleSheetsResultSink(
            spreadsheet_id=google_spreadsheet_id,
            token_provider=token_provider_from_env([SHEETS_SCOPE]),
        )

    handler = _make_handler(candidates_by_id, questions, results_csv, sheet_sink)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Listening for Vapi webhook events on http://{host}:{port}/webhook")
    server.serve_forever()


def _make_handler(
    candidates_by_id: dict[str, Candidate],
    questions: list[Question],
    results_csv: Path,
    sheet_sink: GoogleSheetsResultSink | None,
) -> type[BaseHTTPRequestHandler]:
    class VapiWebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/webhook":
                self.send_error(404, "Not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                event = json.loads(self.rfile.read(length).decode("utf-8"))
                result = result_from_vapi_event(event, candidates_by_id, questions)
                append_result_csv(result, results_csv)
                if sheet_sink:
                    sheet_sink.append(result)
                self._send_json({"ok": True, "candidate_id": result.candidate.candidate_id, "disposition": result.disposition})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return VapiWebhookHandler


def _derive_call_outcome(call: dict, structured: dict, signals: dict) -> str:
    consent = structured.get("consent_status")
    if consent == "declined":
        return "consent_declined"
    if signals.get("requested_do_not_call"):
        return "dnc"
    if signals.get("wrong_number"):
        return "wrong_number"
    if signals.get("candidate_busy"):
        return "busy_callback"

    ended_reason = str(call.get("endedReason") or "").lower()
    if "did-not-answer" in ended_reason or "no-answer" in ended_reason:
        return "no_answer"
    if "busy" in ended_reason:
        return "busy_callback"

    outcome = structured.get("call_outcome")
    if outcome:
        return str(outcome)
    return "completed"


def _special_reason(disposition: str) -> str:
    return {
        "no_answer": "Candidate did not answer.",
        "busy_callback": "Candidate was busy or requested callback.",
        "wrong_number": "Wrong phone number.",
        "dnc": "Candidate requested do not call/removal.",
        "consent_declined": "Candidate declined recording/transcription consent.",
        "incomplete": "Call ended before screening was complete.",
    }.get(disposition, disposition)


def _string_map(value: dict) -> dict[str, str]:
    return {str(key): "" if item is None else str(item) for key, item in value.items()}


def _transcript_url(call: dict) -> str | None:
    artifact = call.get("artifact") or {}
    return (
        artifact.get("transcriptUrl")
        or artifact.get("recordingUrl")
        or call.get("transcriptUrl")
        or call.get("recordingUrl")
    )
