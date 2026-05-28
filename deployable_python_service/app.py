from __future__ import annotations

import os
import shutil
import urllib.error
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from guidewire_screening.api import process_vapi_event_request
from guidewire_screening.business_hours import current_dialing_window, queue_call_payloads
from guidewire_screening.google_auth import token_provider_from_env
from guidewire_screening.google_drive import DRIVE_SCOPE, GoogleDriveRoleFolder
from guidewire_screening.pipeline import build_call_inputs, load_local_role_folder
from guidewire_screening.vapi import build_vapi_call_payload, place_vapi_call


app = FastAPI(title="Role Folder Screening Service")
SERVICE_VERSION = "dialing-window-2026-05-28-1"


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Unhandled service error: {type(exc).__name__}: {exc}",
            "path": request.url.path,
            "service_version": SERVICE_VERSION,
        },
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "role-folder-screening", "service_version": SERVICE_VERSION}


@app.get("/config-check")
def config_check() -> dict[str, Any]:
    return {
        "ok": True,
        "service_version": SERVICE_VERSION,
        "vapi_execute_calls": _bool_value(os.environ.get("VAPI_EXECUTE_CALLS"), False),
        "env_present": {
            "GOOGLE_SERVICE_ACCOUNT_JSON": bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")),
            "GOOGLE_SERVICE_ACCOUNT_JSON_B64": bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64")),
            "GOOGLE_SERVICE_ACCOUNT_JSON_TEXT": bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_TEXT")),
            "VAPI_PHONE_NUMBER_ID": bool(os.environ.get("VAPI_PHONE_NUMBER_ID")),
            "VAPI_ASSISTANT_ID": bool(os.environ.get("VAPI_ASSISTANT_ID")),
            "VAPI_API_KEY": bool(os.environ.get("VAPI_API_KEY")),
            "VAPI_SERVER_URL": bool(os.environ.get("VAPI_SERVER_URL")),
        },
        "dialing_window": current_dialing_window() | {"enabled": True},
    }


@app.post("/drive-event")
def handle_drive_event(body: dict[str, Any]) -> dict[str, Any]:
    role_folder_id = _resolve_role_folder_id(body)
    if not role_folder_id:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve role folder id. Send drive_folder_id, or a Drive event with id/mimeType/parents.",
        )

    execute = _bool_value(body.get("execute"), default=_bool_value(os.environ.get("VAPI_EXECUTE_CALLS"), False))
    limit = int(body["limit"]) if body.get("limit") else None
    candidate_file_id = body.get("candidate_file_id") or _candidate_file_id_from_event(body)
    candidate_file_name = body.get("candidate_file_name") or _candidate_file_name_from_event(body)

    return process_role_folder(
        role_folder_id=role_folder_id,
        execute=execute,
        limit=limit,
        candidate_file_id=candidate_file_id,
        candidate_file_name=candidate_file_name,
    )


@app.post("/preview-folder")
def preview_folder(body: dict[str, Any]) -> dict[str, Any]:
    role_folder_id = body.get("drive_folder_id") or _resolve_role_folder_id(body)
    if not role_folder_id:
        raise HTTPException(status_code=400, detail="drive_folder_id is required")
    limit = int(body["limit"]) if body.get("limit") else None
    return process_role_folder(role_folder_id=role_folder_id, execute=False, limit=limit)


@app.post("/vapi/end-of-call")
def handle_vapi_end_of_call(body: dict[str, Any]) -> dict[str, Any]:
    event = body.get("event") if isinstance(body.get("event"), dict) else body
    metadata = ((event.get("call") or {}).get("metadata") or {}) if isinstance(event, dict) else {}
    drive_folder_id = body.get("drive_folder_id") or metadata.get("drive_folder_id")
    if not drive_folder_id:
        raise HTTPException(status_code=400, detail="drive_folder_id is required in request body or Vapi call metadata")
    return process_vapi_event_request({"drive_folder_id": drive_folder_id, "event": event})


def process_role_folder(
    role_folder_id: str,
    execute: bool,
    limit: int | None = None,
    candidate_file_id: str | None = None,
    candidate_file_name: str | None = None,
) -> dict[str, Any]:
    phone_number_id = os.environ.get("VAPI_PHONE_NUMBER_ID")
    assistant_id = os.environ.get("VAPI_ASSISTANT_ID")
    server_url = os.environ.get("VAPI_SERVER_URL")
    api_key = os.environ.get("VAPI_API_KEY")

    if not phone_number_id:
        raise HTTPException(status_code=500, detail="Set VAPI_PHONE_NUMBER_ID")
    if execute and not api_key:
        raise HTTPException(status_code=500, detail="Set VAPI_API_KEY when VAPI_EXECUTE_CALLS=true")

    try:
        folder = _download_role_folder(role_folder_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=502,
            detail=f"Google Drive request failed with HTTP {exc.code}: {body}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to download Drive folder {role_folder_id}: {exc}") from exc

    try:
        jd, questions, candidates = load_local_role_folder(folder)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse role folder {role_folder_id}: {exc}") from exc

    if candidate_file_id or candidate_file_name:
        candidates = [
            candidate
            for candidate in candidates
            if (candidate_file_id and candidate.drive_file_id == candidate_file_id)
            or (candidate_file_name and candidate.source_file.name == candidate_file_name)
        ]

    call_inputs = build_call_inputs(candidates, jd, questions, questionnaire_file_id="drive-folder")
    if limit:
        call_inputs = call_inputs[:limit]

    payloads = []
    for item in call_inputs:
        payload = build_vapi_call_payload(
            item,
            phone_number_id=phone_number_id,
            server_url=server_url,
            assistant_id=assistant_id,
        )
        payload["metadata"]["drive_folder_id"] = role_folder_id
        payloads.append(payload)

    if not execute:
        return {
            "status": "payloads_built",
            "role_folder_id": role_folder_id,
            "role_title": jd.title,
            "candidate_count": len(candidates),
            "question_count": len(questions),
            "payload_count": len(payloads),
            "payloads": payloads,
        }

    dialing_window = current_dialing_window()
    if not dialing_window["is_open"]:
        queue_path = queue_call_payloads(
            payloads,
            scheduled_for=dialing_window["scheduled_for"],
            reason="outside_business_hours",
        )
        return {
            "status": "calls_queued",
            "role_folder_id": role_folder_id,
            "role_title": jd.title,
            "candidate_count": len(candidates),
            "question_count": len(questions),
            "payload_count": len(payloads),
            "dialing_window": dialing_window,
            "queue_file": str(queue_path),
        }

    try:
        responses = [place_vapi_call(payload, api_key=api_key) for payload in payloads]
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "status": "calls_started",
        "role_folder_id": role_folder_id,
        "role_title": jd.title,
        "candidate_count": len(candidates),
        "question_count": len(questions),
        "payload_count": len(payloads),
        "responses": responses,
    }


def _download_role_folder(role_folder_id: str) -> Path:
    cache_root = Path(os.environ.get("ROLE_SCREENING_WORK_DIR", "/tmp/role-screening"))
    destination = cache_root / role_folder_id
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    provider = token_provider_from_env([DRIVE_SCOPE])
    return GoogleDriveRoleFolder(provider).download_folder(role_folder_id, destination)


def _resolve_role_folder_id(body: dict[str, Any]) -> str | None:
    source = body.get("body") if isinstance(body.get("body"), dict) else body
    if source.get("drive_folder_id"):
        return str(source["drive_folder_id"])
    mime_type = source.get("mimeType") or source.get("mime_type") or ""
    if mime_type == "application/vnd.google-apps.folder":
        return str(source.get("id") or "") or None
    parents = source.get("parents") or source.get("parentIds") or []
    if isinstance(parents, list) and parents:
        return str(parents[0])
    return None


def _candidate_file_id_from_event(body: dict[str, Any]) -> str | None:
    source = body.get("body") if isinstance(body.get("body"), dict) else body
    name = source.get("name") or source.get("fileName") or ""
    mime_type = source.get("mimeType") or source.get("mime_type") or ""
    if _is_resume(name, mime_type):
        return source.get("id")
    return None


def _candidate_file_name_from_event(body: dict[str, Any]) -> str | None:
    source = body.get("body") if isinstance(body.get("body"), dict) else body
    name = source.get("name") or source.get("fileName") or ""
    mime_type = source.get("mimeType") or source.get("mime_type") or ""
    return name if _is_resume(name, mime_type) else None


def _is_resume(name: str, mime_type: str) -> bool:
    lower = name.lower()
    if "jd" in lower or "job description" in lower:
        return False
    return lower.endswith((".pdf", ".docx")) or mime_type in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.document",
    }


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}
