from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .models import CandidateCallInput
from .prompting import (
    build_monica_prompt,
    build_structured_output_schema,
    format_qualification_questions,
)


VAPI_BASE_URL = "https://api.vapi.ai"
VAPI_USER_AGENT = "adactin-role-screening/1.0"


def build_vapi_call_payload(
    call_input: CandidateCallInput,
    phone_number_id: str,
    server_url: str | None = None,
    assistant_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phoneNumberId": phone_number_id,
        "customer": {
            "number": call_input.phone,
        },
        "metadata": {
            "candidate_id": call_input.candidate_id,
            "cv_file_id": call_input.cv_file_id,
            "jd_file_id": call_input.jd_file_id,
            "questionnaire_file_id": call_input.questionnaire_file_id,
            "role_title": call_input.role_title,
        },
    }
    if call_input.name_if_known:
        payload["customer"]["name"] = call_input.name_if_known
    if assistant_id:
        payload["assistantId"] = assistant_id
        payload["assistantOverrides"] = {
            "variableValues": {
                "role_title": call_input.role_title,
                "qualification_questions": format_qualification_questions(call_input.questions),
            }
        }
    else:
        assistant: dict[str, Any] = {
            "name": f"Monica - {call_input.role_title} - {call_input.candidate_id}",
            "firstMessage": f"Hi there, this is Monica calling from Adactin about a {call_input.role_title} role.",
            "model": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "messages": [{"role": "system", "content": build_monica_prompt(call_input)}],
            },
            "analysisPlan": {
                "summaryPrompt": "Summarize the recruitment screening call in 2 to 4 concise recruiter-facing sentences.",
                "structuredDataPrompt": "Extract the candidate's answers exactly from the screening transcript. Use question ids from the questionnaire.",
                "structuredDataSchema": build_structured_output_schema(call_input.questions),
            },
            "maxDurationSeconds": 900,
        }
        if server_url:
            assistant["server"] = {"url": server_url}
        payload["assistant"] = assistant
    return payload


def place_vapi_call(payload: dict[str, Any], api_key: str, base_url: str = VAPI_BASE_URL) -> dict[str, Any]:
    request = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/call",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": VAPI_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vapi call request failed with HTTP {exc.code}: {body}") from exc
