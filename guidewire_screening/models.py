from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    qualifying_rules: list[str]


@dataclass(frozen=True)
class JobDescription:
    source_file: Path
    title: str
    text: str
    summary: str
    drive_file_id: str | None = None


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    name: str | None
    phone: str
    source_file: Path
    text: str
    summary: str
    email: str | None = None
    drive_file_id: str | None = None
    screening_facts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateCallInput:
    candidate_id: str
    name_if_known: str | None
    phone: str
    cv_file_id: str
    jd_file_id: str
    questionnaire_file_id: str
    cv_summary: str
    role_title: str
    role_summary: str
    questions: list[Question]


@dataclass(frozen=True)
class QualificationResult:
    disposition: str
    failed_question_ids: list[str]
    failure_reasons: list[str]
    normalized_answers: dict[str, str]
    per_question_pass: dict[str, bool]
