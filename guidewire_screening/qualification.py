from __future__ import annotations

import re

from .models import QualificationResult, Question


NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def evaluate_candidate(questions: list[Question], answers: dict[str, str]) -> QualificationResult:
    failed_ids: list[str] = []
    failure_reasons: list[str] = []
    normalized_answers: dict[str, str] = {}
    per_question_pass: dict[str, bool] = {}

    for question in questions:
        answer = answers.get(question.id, "")
        normalized = normalize_answer(question.id, answer)
        normalized_answers[question.id] = normalized
        passed, reason = evaluate_question(question, answer)
        per_question_pass[question.id] = passed
        if not passed:
            failed_ids.append(question.id)
            failure_reasons.append(reason)

    disposition = "qualified" if not failed_ids else "not_qualified"
    return QualificationResult(
        disposition=disposition,
        failed_question_ids=failed_ids,
        failure_reasons=failure_reasons,
        normalized_answers=normalized_answers,
        per_question_pass=per_question_pass,
    )


def normalize_answer(question_id: str, answer: str) -> str:
    lower = answer.lower().strip()
    if question_id == "q004":
        if any(term in lower for term in ["citizen", "permanent resident", " pr", "pr ", "working visa", "work visa"]):
            return "citizen_or_pr_or_working_visa"
        if "working" in lower and "visa" in lower:
            return "citizen_or_pr_or_working_visa"
        return "other_work_rights"
    if question_id in {"q012", "q014"}:
        return "yes" if is_affirmative(lower) else "no"
    if question_id == "q011":
        modules = []
        if "policycenter" in lower or "policy center" in lower or " pc" in f" {lower}":
            modules.append("PolicyCenter")
        if "billingcenter" in lower or "billing center" in lower or " bc" in f" {lower}":
            modules.append("BillingCenter")
        if "claimcenter" in lower or "claim center" in lower or " cc" in f" {lower}":
            modules.append("ClaimCenter")
        return ", ".join(modules) if modules else "none"
    return re.sub(r"\s+", " ", answer.strip())


def evaluate_question(question: Question, answer: str) -> tuple[bool, str]:
    if question.id in {"q001", "q002", "q003"}:
        return True, ""
    if question.id in {"q015", "q016"}:
        return True, ""
    if not question.qualifying_rules:
        return True, ""

    lower = answer.lower().strip()
    if not lower:
        return False, f"{question.id}: missing answer for {question.text}"

    if question.id == "q004":
        passed = normalize_answer(question.id, answer) == "citizen_or_pr_or_working_visa"
        return passed, "" if passed else f"{question.id}: work rights do not match PR, citizenship, or working visa"

    if question.id == "q005":
        passed = any(term in lower for term in ["contract", "permanent", "both"])
        return passed, "" if passed else f"{question.id}: opportunity preference is not contract, permanent, or both"

    if question.id in {"q006", "q007"}:
        return True, ""

    if question.id == "q008":
        overall, guidewire = extract_overall_and_relevant_years(answer)
        failures = []
        if overall < 8:
            failures.append("overall experience below 8 years")
        if guidewire < 2:
            failures.append("Guidewire experience below 2 years")
        return not failures, "" if not failures else f"{question.id}: " + "; ".join(failures)

    if question.id == "q011":
        modules = normalize_answer(question.id, answer)
        passed = modules != "none"
        return passed, "" if passed else f"{question.id}: no PolicyCenter, BillingCenter, or ClaimCenter module experience"

    if question.id == "q012":
        passed = is_affirmative(lower)
        return passed, "" if passed else f"{question.id}: current or most recent role is not Guidewire"

    if question.id == "q013":
        years = extract_years(answer)
        passed = years > 1
        return passed, "" if passed else f"{question.id}: configuration and integration experience is not above 1 year"

    if question.id == "q014":
        passed = is_affirmative(lower)
        return passed, "" if passed else f"{question.id}: no Gosu programming experience"

    return True, ""


def is_affirmative(value: str) -> bool:
    negative_terms = ["no", "not", "never", "haven't", "have not", "none"]
    if any(re.search(rf"\b{re.escape(term)}\b", value) for term in negative_terms):
        return False
    return any(re.search(rf"\b{term}\b", value) for term in ["yes", "yeah", "yep", "sure", "regularly", "hands-on", "have", "use"])


def extract_overall_and_relevant_years(answer: str) -> tuple[float, float]:
    lower = replace_number_words(answer.lower())
    numbers = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", lower)]
    if not numbers:
        return 0.0, 0.0

    overall = _number_near(lower, ["overall", "total"], default=numbers[0])
    guidewire = _number_near(lower, ["guidewire", "relevant"], default=numbers[1] if len(numbers) > 1 else 0.0)
    return overall, guidewire


def extract_years(answer: str) -> float:
    lower = replace_number_words(answer.lower())
    match = re.search(r"less than\s+(\d+(?:\.\d+)?)", lower)
    if match:
        return max(float(match.group(1)) - 0.5, 0.0)
    match = re.search(r"(\d+(?:\.\d+)?)", lower)
    return float(match.group(1)) if match else 0.0


def replace_number_words(value: str) -> str:
    for word, number in NUMBER_WORDS.items():
        value = re.sub(rf"\b{word}\b", str(number), value)
    return value


def _number_near(text: str, keywords: list[str], default: float) -> float:
    for keyword in keywords:
        before = re.search(rf"(\d+(?:\.\d+)?)\s+[^.／/,\n]{{0,40}}\b{keyword}\b", text)
        if before:
            return float(before.group(1))
        after = re.search(rf"\b{keyword}\b[^.／/,\n]{{0,40}}?(\d+(?:\.\d+)?)", text)
        if after:
            return float(after.group(1))
    return default
