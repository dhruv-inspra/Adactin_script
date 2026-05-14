from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from guidewire_screening.models import Candidate, CandidateCallInput, Question
from guidewire_screening.parsers import (
    extract_screening_answers_from_cv,
    parse_candidates_from_folder,
    parse_jd,
    parse_questionnaire,
)
from guidewire_screening.pipeline import build_call_inputs
from guidewire_screening.prompting import build_monica_prompt, build_structured_output_schema
from guidewire_screening.qualification import evaluate_candidate
from guidewire_screening.results import ScreeningResult, write_results_csv
from guidewire_screening.role_questions import build_role_questions
from guidewire_screening.webhook import result_from_vapi_event
from guidewire_screening.vapi import build_vapi_call_payload


ROOT = Path(__file__).resolve().parents[1]


class ScreeningWorkflowTests(unittest.TestCase):
    def test_parse_questionnaire_uses_workbook_questions_only(self) -> None:
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")

        self.assertEqual(len(questions), 14)
        self.assertEqual(questions[0].text, "Name")
        self.assertEqual(questions[-1].text, "What is your salary expectation?")
        self.assertTrue(any("Overall experience" in rule for rule in questions[7].qualifying_rules))
        self.assertTrue(any("Relevant experience" in rule for rule in questions[7].qualifying_rules))
        self.assertFalse(any("Reason for job change" in q.text for q in questions))
        self.assertFalse(any("planned leave" in q.text.lower() for q in questions))
        self.assertFalse(any("current project" in q.text.lower() for q in questions))
        self.assertFalse(any("certification" in q.text.lower() for q in questions))

    def test_parse_candidates_from_sample_folder_extracts_all_phone_numbers(self) -> None:
        candidates = parse_candidates_from_folder(ROOT)

        self.assertEqual(len(candidates), 5)
        by_file = {candidate.source_file.name: candidate for candidate in candidates}
        self.assertEqual(by_file["dhairya Pahwa_CV.docx"].phone, "+61421369211")
        self.assertEqual(by_file["Ramya_CV.pdf"].phone, "+61458541865")
        self.assertEqual(by_file["Rakhi_CV.pdf"].phone, "+61272387353")
        self.assertEqual(by_file["Sadhana_CV.docx"].phone, "+61493211445")
        self.assertEqual(by_file["Sapna_CV.docx"].phone, "+61420983561")
        self.assertEqual(by_file["Ramya_CV.pdf"].screening_facts["q014"], "No")
        self.assertIn("3 years overall", by_file["Ramya_CV.pdf"].screening_facts["q008"])
        self.assertEqual(by_file["Rakhi_CV.pdf"].screening_facts["q013"], "4 years")

    def test_screening_facts_from_cv_can_be_pre_evaluated_for_demo_preview(self) -> None:
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        candidates = parse_candidates_from_folder(ROOT)
        by_file = {candidate.source_file.name: candidate for candidate in candidates}

        ramya_preview = evaluate_candidate(questions, by_file["Ramya_CV.pdf"].screening_facts)
        sapna_preview = evaluate_candidate(questions, by_file["Sapna_CV.docx"].screening_facts)

        self.assertEqual(ramya_preview.disposition, "not_qualified")
        self.assertIn("q014", ramya_preview.failed_question_ids)
        self.assertEqual(sapna_preview.disposition, "qualified")

    def test_parse_candidates_uses_drive_manifest_file_ids_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            shutil.copy(ROOT / "Ramya_CV.pdf", tmp / "Ramya_CV.pdf")
            (tmp / ".drive_manifest.json").write_text(
                json.dumps({"Ramya_CV.pdf": {"id": "drive-file-123"}}),
                encoding="utf-8",
            )

            candidates = parse_candidates_from_folder(tmp)

        self.assertEqual(candidates[0].drive_file_id, "drive-file-123")

    def test_build_call_inputs_combines_jd_questions_and_candidate_context(self) -> None:
        jd = parse_jd(ROOT / "Guidewire_Developer_JD.docx")
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        candidates = parse_candidates_from_folder(ROOT)

        call_inputs = build_call_inputs(candidates, jd, questions)

        self.assertEqual(len(call_inputs), 5)
        first = call_inputs[0]
        self.assertEqual(first.role_title, "Guidewire Developer")
        self.assertEqual(len(first.questions), 14)
        self.assertIn("Guidewire", first.cv_summary)
        self.assertTrue(first.phone.startswith("+61"))

    def test_jd_adds_role_specific_questions_to_base_questionnaire(self) -> None:
        jd = parse_jd(ROOT / "Guidewire_Developer_JD.docx")
        role_questions = build_role_questions(jd)

        self.assertGreaterEqual(len(role_questions), 1)
        self.assertLessEqual(len(role_questions), 5)
        self.assertTrue(all(question.id.startswith("role") for question in role_questions))
        self.assertTrue(any("Guidewire" in question.text or "Gosu" in question.text for question in role_questions))

    def test_qualification_passes_strong_candidate_and_fails_missing_must_haves(self) -> None:
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        strong_answers = {
            "q004": "Australian permanent resident",
            "q005": "Both contract and permanent",
            "q006": "No",
            "q007": "Immediate",
            "q008": "Nine years total experience and three years as a Guidewire Developer",
            "q011": "PolicyCenter and ClaimCenter",
            "q012": "Yes, my current role is Guidewire",
            "q013": "Three years in configuration and integration",
            "q014": "Yes, I use Gosu regularly",
            "q015": "AUD 110 per hour",
            "q016": "AUD 125 per hour",
        }
        weak_answers = strong_answers | {
            "q008": "Three years total and one year in Guidewire",
            "q013": "Less than one year",
            "q014": "No",
        }

        strong = evaluate_candidate(questions, strong_answers)
        weak = evaluate_candidate(questions, weak_answers)

        self.assertEqual(strong.disposition, "qualified")
        self.assertEqual(strong.failure_reasons, [])
        self.assertEqual(weak.disposition, "not_qualified")
        self.assertIn("q008", weak.failed_question_ids)
        self.assertIn("q013", weak.failed_question_ids)
        self.assertIn("q014", weak.failed_question_ids)

    def test_prompt_uses_dynamic_questions_and_removes_old_tooling_and_extra_questions(self) -> None:
        jd = parse_jd(ROOT / "Guidewire_Developer_JD.docx")
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        candidate = CandidateCallInput(
            candidate_id="candidate-test",
            name_if_known="Sarah Patel",
            phone="+61400000000",
            cv_file_id="cv-local",
            jd_file_id="jd-local",
            questionnaire_file_id="questionnaire-local",
            cv_summary="Senior Guidewire developer with PolicyCenter and Gosu.",
            role_title=jd.title,
            role_summary=jd.summary,
            questions=questions,
        )

        prompt = build_monica_prompt(candidate)

        self.assertIn("Monica", prompt)
        self.assertIn("explicit consent", prompt.lower())
        self.assertIn("What is your salary expectation?", prompt)
        self.assertNotIn("tag_candidate", prompt)
        self.assertNotIn("Approved", prompt)
        self.assertNotIn("Rejected", prompt)
        self.assertNotIn("Reason for job change", prompt)
        self.assertNotIn("planned leave", prompt.lower())
        self.assertNotIn("current project", prompt.lower())
        self.assertNotIn("certification", prompt.lower())
        self.assertIn("Do not tell the candidate whether they qualify", prompt)

    def test_vapi_payload_contains_transient_assistant_and_structured_output_schema(self) -> None:
        jd = parse_jd(ROOT / "Guidewire_Developer_JD.docx")
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        candidate = CandidateCallInput(
            candidate_id="candidate-test",
            name_if_known="Sarah Patel",
            phone="+61400000000",
            cv_file_id="cv-local",
            jd_file_id="jd-local",
            questionnaire_file_id="questionnaire-local",
            cv_summary="Senior Guidewire developer with PolicyCenter and Gosu.",
            role_title=jd.title,
            role_summary=jd.summary,
            questions=questions,
        )

        payload = build_vapi_call_payload(
            candidate,
            phone_number_id="phone-number-id",
            server_url="https://example.com/vapi/webhook",
        )

        self.assertEqual(payload["phoneNumberId"], "phone-number-id")
        self.assertEqual(payload["customer"]["number"], "+61400000000")
        self.assertEqual(payload["customer"]["name"], "Sarah Patel")
        self.assertEqual(payload["metadata"]["candidate_id"], "candidate-test")
        self.assertIn("assistant", payload)
        self.assertIn("analysisPlan", payload["assistant"])
        schema = payload["assistant"]["analysisPlan"]["structuredDataSchema"]
        self.assertIn("answers", schema["properties"])
        self.assertIn("disposition_signals", schema["properties"])

    def test_vapi_payload_can_use_saved_assistant_with_questions_variable(self) -> None:
        jd = parse_jd(ROOT / "Guidewire_Developer_JD.docx")
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        candidate = CandidateCallInput(
            candidate_id="candidate-test",
            name_if_known="Sarah Patel",
            phone="+61400000000",
            cv_file_id="cv-local",
            jd_file_id="jd-local",
            questionnaire_file_id="questionnaire-local",
            cv_summary="Senior Guidewire developer with PolicyCenter and Gosu.",
            role_title=jd.title,
            role_summary=jd.summary,
            questions=questions,
        )

        payload = build_vapi_call_payload(
            candidate,
            phone_number_id="phone-number-id",
            assistant_id="assistant-id",
        )

        self.assertEqual(payload["assistantId"], "assistant-id")
        self.assertNotIn("assistant", payload)
        variables = payload["assistantOverrides"]["variableValues"]
        self.assertIn("qualification_questions", variables)
        self.assertIn("q014: Do you have experience with Gosu programming?", variables["qualification_questions"])

    def test_result_csv_contains_disposition_and_per_question_evidence(self) -> None:
        candidate = Candidate(
            candidate_id="candidate-test",
            name="Sarah Patel",
            phone="+61400000000",
            source_file=ROOT / "Sarah_CV.pdf",
            text="",
            summary="Senior Guidewire developer.",
            email="sarah@example.com",
            screening_facts={"q014": "Yes"},
        )
        result = ScreeningResult(
            candidate=candidate,
            call_id="call-123",
            disposition="qualified",
            call_outcome="completed",
            summary="Candidate answered all questions.",
            answers={"q004": "PR", "q014": "Yes"},
            normalized_answers={"q004": "citizen_or_pr_or_working_visa", "q014": "yes"},
            per_question_pass={"q004": True, "q014": True},
            failure_reasons=[],
            transcript_url="https://example.com/transcript",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "results.csv"
            write_results_csv([result], output)
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(rows[0]["candidate_id"], "candidate-test")
        self.assertEqual(rows[0]["disposition"], "qualified")
        self.assertEqual(rows[0]["call_id"], "call-123")
        self.assertEqual(json.loads(rows[0]["answers"])["q014"], "Yes")
        self.assertEqual(json.loads(rows[0]["per_question_pass"])["q004"], True)
        self.assertEqual(json.loads(rows[0]["parsed_cv_facts"])["q014"], "Yes")

    def test_structured_output_schema_accepts_question_answer_objects(self) -> None:
        questions = [
            Question("q001", "Name", []),
            Question("q014", "Do you have experience with Gosu programming?", ["Yes"]),
        ]

        schema = build_structured_output_schema(questions)

        self.assertEqual(schema["type"], "object")
        self.assertIn("answers", schema["required"])
        self.assertEqual(schema["properties"]["answers"]["additionalProperties"]["type"], "string")

    def test_vapi_end_of_call_event_becomes_evaluated_screening_result(self) -> None:
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        candidate = Candidate(
            candidate_id="candidate-test",
            name="Sarah Patel",
            phone="+61400000000",
            source_file=ROOT / "Sarah_CV.pdf",
            text="",
            summary="Senior Guidewire developer.",
        )
        event = {
            "type": "end-of-call-report",
            "call": {
                "id": "call-123",
                "metadata": {"candidate_id": "candidate-test"},
                "analysis": {
                    "structuredData": {
                        "consent_status": "accepted",
                        "call_outcome": "completed",
                        "answers": {
                            "q004": "Australian permanent resident",
                            "q005": "Permanent",
                            "q006": "No",
                            "q007": "Immediate",
                            "q008": "Nine years total and three years Guidewire",
                            "q011": "PolicyCenter",
                            "q012": "Yes",
                            "q013": "Three years",
                            "q014": "Yes",
                        },
                        "disposition_signals": {
                            "requested_do_not_call": False,
                            "wrong_number": False,
                            "candidate_busy": False,
                            "human_follow_up_requested": False,
                        },
                        "summary": "Candidate completed the screening.",
                    }
                },
            },
        }

        result = result_from_vapi_event(event, {"candidate-test": candidate}, questions)

        self.assertEqual(result.call_id, "call-123")
        self.assertEqual(result.disposition, "qualified")
        self.assertEqual(result.call_outcome, "completed")
        self.assertEqual(result.answers["q014"], "Yes")

    def test_vapi_end_of_call_event_respects_consent_decline_before_qualification(self) -> None:
        questions = parse_questionnaire(ROOT / "Questions Template.xlsx")
        candidate = Candidate(
            candidate_id="candidate-test",
            name="Sarah Patel",
            phone="+61400000000",
            source_file=ROOT / "Sarah_CV.pdf",
            text="",
            summary="Senior Guidewire developer.",
        )
        event = {
            "call": {
                "id": "call-456",
                "metadata": {"candidate_id": "candidate-test"},
                "analysis": {
                    "structuredData": {
                        "consent_status": "declined",
                        "call_outcome": "consent_declined",
                        "answers": {},
                        "disposition_signals": {},
                        "summary": "Candidate declined consent.",
                    }
                },
            }
        }

        result = result_from_vapi_event(event, {"candidate-test": candidate}, questions)

        self.assertEqual(result.disposition, "consent_declined")
        self.assertEqual(result.call_outcome, "consent_declined")
        self.assertEqual(result.failure_reasons, ["Candidate declined recording/transcription consent."])


if __name__ == "__main__":
    unittest.main()
