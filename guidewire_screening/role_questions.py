from __future__ import annotations

import re

from .models import JobDescription, Question


ROLE_QUESTION_LIMIT = 5


def build_role_questions(jd: JobDescription, limit: int = ROLE_QUESTION_LIMIT) -> list[Question]:
    """Create interview questions from JD-specific requirements."""
    requirements = _extract_requirement_lines(jd.text)
    if not requirements:
        requirements = _extract_requirement_lines(jd.summary)

    questions: list[Question] = []
    seen: set[str] = set()
    for line in requirements:
        topic = _question_topic(line)
        key = topic.lower()
        if not topic or key in seen:
            continue
        seen.add(key)
        questions.append(
            Question(
                id=f"role{len(questions) + 1:03d}",
                text=f"Can you briefly describe your hands-on experience with {topic}?",
                qualifying_rules=[],
            )
        )
        if len(questions) >= limit:
            break

    return questions


def _extract_requirement_lines(text: str) -> list[str]:
    lines = [line.strip(" \t-*•") for line in text.splitlines()]
    filtered: list[str] = []
    in_relevant_section = False
    for line in lines:
        if not line:
            continue
        lower = line.lower()
        if lower in {"highly desirable", "why join us", "how to apply"}:
            in_relevant_section = False
        if any(marker in lower for marker in ["essential", "required", "responsibilities", "requirements", "skills"]):
            in_relevant_section = True
            continue
        if in_relevant_section and _looks_like_requirement(line):
            filtered.append(line)

    if filtered:
        return filtered
    return [line for line in lines if _looks_like_requirement(line)]


def _looks_like_requirement(line: str) -> bool:
    if len(line) < 18 or len(line) > 180:
        return False
    lower = line.lower()
    useful_terms = [
        "experience",
        "knowledge",
        "develop",
        "configure",
        "integration",
        "api",
        "cloud",
        "sql",
        "java",
        "testing",
        "agile",
        "guidewire",
        "gosu",
    ]
    return any(term in lower for term in useful_terms)


def _question_topic(line: str) -> str:
    cleaned = re.sub(r"\s+", " ", line).strip(" .;:")
    cleaned = re.sub(r"(?i)^(must have|should have|required|strong|good|hands-on)\s+", "", cleaned)
    cleaned = re.sub(r"(?i)\b(experience|knowledge|skills?)\b\s+(in|with|of)\s+", "", cleaned)
    cleaned = re.sub(r"(?i)\b\d+\+?\s+years?\s+(of\s+)?", "", cleaned)
    cleaned = cleaned.strip(" .;:")
    if len(cleaned) > 90:
        cleaned = cleaned[:87].rstrip(" ,.;:") + "..."
    return cleaned
