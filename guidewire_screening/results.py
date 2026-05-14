from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Candidate


RESULT_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "phone",
    "email",
    "source_file",
    "parsed_cv_facts",
    "call_id",
    "disposition",
    "call_outcome",
    "summary",
    "answers",
    "normalized_answers",
    "per_question_pass",
    "failure_reasons",
    "transcript_url",
]


@dataclass(frozen=True)
class ScreeningResult:
    candidate: Candidate
    call_id: str
    disposition: str
    call_outcome: str
    summary: str
    answers: dict[str, str]
    normalized_answers: dict[str, str]
    per_question_pass: dict[str, bool]
    failure_reasons: list[str]
    transcript_url: str | None = None


def write_results_csv(results: list[ScreeningResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for result in results:
            writer.writerow(screening_result_to_row(result))


def append_result_csv(result: ScreeningResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    exists = output_path.exists()
    with output_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(screening_result_to_row(result))


def screening_result_to_row(result: ScreeningResult) -> dict[str, str]:
    candidate = result.candidate
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_name": candidate.name or "",
        "phone": candidate.phone,
        "email": candidate.email or "",
        "source_file": str(candidate.source_file),
        "parsed_cv_facts": json.dumps(candidate.screening_facts, ensure_ascii=False, sort_keys=True),
        "call_id": result.call_id,
        "disposition": result.disposition,
        "call_outcome": result.call_outcome,
        "summary": result.summary,
        "answers": json.dumps(result.answers, ensure_ascii=False, sort_keys=True),
        "normalized_answers": json.dumps(result.normalized_answers, ensure_ascii=False, sort_keys=True),
        "per_question_pass": json.dumps(result.per_question_pass, ensure_ascii=False, sort_keys=True),
        "failure_reasons": json.dumps(result.failure_reasons, ensure_ascii=False),
        "transcript_url": result.transcript_url or "",
    }


def extract_structured_output(event: dict[str, Any]) -> dict[str, Any]:
    call = event.get("call", event)
    analysis = call.get("analysis") or {}
    if isinstance(analysis.get("structuredData"), dict):
        return analysis["structuredData"]

    artifact = call.get("artifact") or {}
    structured_outputs = artifact.get("structuredOutputs") or {}
    for value in structured_outputs.values():
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            return value["result"]
    return {}
