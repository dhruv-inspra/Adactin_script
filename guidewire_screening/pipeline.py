from __future__ import annotations

from pathlib import Path

from .models import Candidate, CandidateCallInput, JobDescription, Question
from .parsers import parse_candidates_from_folder, parse_jd, parse_questionnaire
from .role_questions import build_role_questions


def find_role_files(folder: Path) -> tuple[Path, Path | None]:
    jd_candidates = sorted(folder.glob("*JD*.docx")) + sorted(folder.glob("*Job*Description*.docx"))
    questionnaire_candidates = sorted(folder.glob("*.xlsx"))
    if not jd_candidates:
        raise FileNotFoundError(f"No JD .docx file found in {folder}")
    return jd_candidates[0], questionnaire_candidates[0] if questionnaire_candidates else None


def load_local_role_folder(folder: Path) -> tuple[JobDescription, list[Question], list[Candidate]]:
    jd_path, questionnaire_path = find_role_files(folder)
    jd = parse_jd(jd_path)
    questions = (
        parse_questionnaire(questionnaire_path)
        if questionnaire_path
        else build_default_qualification_questions()
    )
    questions += build_role_questions(jd)
    candidates = parse_candidates_from_folder(folder)
    return jd, questions, candidates


def build_default_qualification_questions() -> list[Question]:
    return [
        Question("q001", "What is your first and last name?", []),
        Question("q002", "Where are you currently located?", []),
        Question("q003", "What is the best email address for the recruitment team?", []),
        Question("q004", "What are your current work rights?", ["Citizen, PR, or valid working visa"]),
        Question("q005", "Are you looking for contract, permanent, or both types of opportunities?", ["Contract, permanent, or both"]),
        Question("q006", "Are you currently holding any offers?", []),
        Question("q007", "What is your notice period?", []),
        Question("q008", "What is your total experience, and your relevant Guidewire experience?", ["Overall experience above 8 years", "Relevant Guidewire experience above 2 years"]),
        Question("q011", "Which Guidewire modules have you worked on, PolicyCenter, BillingCenter, or ClaimCenter?", ["PolicyCenter, BillingCenter, or ClaimCenter"]),
        Question("q012", "Is your current or most recent role a Guidewire role?", ["Yes"]),
        Question("q013", "What is your relevant experience in Guidewire configuration and integration?", ["More than 1 year"]),
        Question("q014", "Do you have experience with Gosu programming?", ["Yes"]),
        Question("q015", "What is your current salary or rate?", []),
        Question("q016", "What is your salary or rate expectation?", []),
    ]


def build_call_inputs(
    candidates: list[Candidate],
    jd: JobDescription,
    questions: list[Question],
    questionnaire_file_id: str | None = None,
) -> list[CandidateCallInput]:
    return [
        CandidateCallInput(
            candidate_id=candidate.candidate_id,
            name_if_known=candidate.name,
            phone=candidate.phone,
            cv_file_id=candidate.drive_file_id or str(candidate.source_file),
            jd_file_id=jd.drive_file_id or str(jd.source_file),
            questionnaire_file_id=questionnaire_file_id or "local-questionnaire",
            cv_summary=candidate.summary,
            role_title=jd.title,
            role_summary=jd.summary,
            questions=questions,
        )
        for candidate in candidates
    ]
