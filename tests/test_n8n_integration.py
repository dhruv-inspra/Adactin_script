from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from guidewire_screening.business_hours import current_dialing_window
from guidewire_screening.api import preview_request, process_vapi_event_request, start_calls_request


ROOT = Path(__file__).resolve().parents[1]


class N8nIntegrationTests(unittest.TestCase):
    def test_preview_request_returns_candidates_questions_and_dispositions(self) -> None:
        response = preview_request({"local_folder": str(ROOT)})

        self.assertEqual(response["role_title"], "Guidewire Developer")
        self.assertEqual(response["candidate_count"], 5)
        self.assertGreater(response["question_count"], 14)
        dispositions = {candidate["name"]: candidate["cv_predicted_disposition"] for candidate in response["candidates"]}
        self.assertEqual(dispositions["Sapna"], "qualified")
        self.assertEqual(dispositions["Ramya"], "not_qualified")

    def test_start_calls_request_builds_payloads_without_executing_by_default(self) -> None:
        response = start_calls_request(
            {
                "local_folder": str(ROOT),
                "phone_number_id": "phone-number-id",
                "server_url": "https://n8n.example.com/webhook/guidewire/vapi-end",
                "limit": 1,
                "execute": False,
            }
        )

        self.assertEqual(response["status"], "payloads_built")
        self.assertEqual(response["payload_count"], 1)
        payload = response["payloads"][0]
        self.assertEqual(payload["phoneNumberId"], "phone-number-id")
        self.assertEqual(payload["assistant"]["server"]["url"], "https://n8n.example.com/webhook/guidewire/vapi-end")
        self.assertIn(
            "role001",
            payload["assistant"]["analysisPlan"]["structuredDataSchema"]["properties"]["answers"]["description"],
        )

    def test_start_calls_can_filter_to_changed_candidate_resume(self) -> None:
        response = start_calls_request(
            {
                "local_folder": str(ROOT),
                "phone_number_id": "phone-number-id",
                "candidate_file_name": "Ramya_CV.pdf",
                "execute": False,
            }
        )

        self.assertEqual(response["payload_count"], 1)
        self.assertEqual(response["payloads"][0]["customer"]["number"], "+61458541865")

    def test_outside_business_hours_queues_calls_instead_of_dialing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_file = Path(tmpdir) / "call_queue.jsonl"
            with patch("guidewire_screening.api.current_dialing_window") as window:
                window.return_value = {
                    "is_open": False,
                    "scheduled_for": "2026-05-15T09:00:00+05:30",
                    "business_hours": "Mon-Fri 09:00-18:00",
                    "timezone": "Asia/Kolkata",
                }
                with patch.dict(os.environ, {"CALL_QUEUE_FILE": str(queue_file)}):
                    response = start_calls_request(
                        {
                            "local_folder": str(ROOT),
                            "phone_number_id": "phone-number-id",
                            "api_key": "api-key",
                            "execute": True,
                            "limit": 1,
                        }
                    )
            queued_text = queue_file.read_text(encoding="utf-8").strip()

        self.assertEqual(response["status"], "calls_queued")
        self.assertEqual(response["scheduled_for"], "2026-05-15T09:00:00+05:30")
        self.assertEqual(response["payload_count"], 1)
        self.assertTrue(queued_text)

    def test_business_hours_next_window_after_6pm_is_next_working_day(self) -> None:
        after_hours = datetime(2026, 5, 14, 18, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))

        window = current_dialing_window(after_hours)

        self.assertFalse(window["is_open"])
        self.assertTrue(window["scheduled_for"].startswith("2026-05-15T09:00:00"))

    def test_vapi_event_request_evaluates_and_returns_result_row(self) -> None:
        event = {
            "call": {
                "id": "call-n8n-1",
                "metadata": {"candidate_id": "candidate-sapna-cv"},
                "analysis": {
                    "structuredData": {
                        "consent_status": "accepted",
                        "call_outcome": "completed",
                        "answers": {
                            "q004": "Australian permanent resident",
                            "q005": "Permanent",
                            "q006": "No",
                            "q007": "Less than one month",
                            "q008": "Ten years total and six years Guidewire",
                            "q011": "PolicyCenter and BillingCenter",
                            "q012": "Yes",
                            "q013": "Five years",
                            "q014": "Yes",
                        },
                        "disposition_signals": {},
                        "summary": "Candidate completed the screening.",
                    }
                },
            }
        }

        response = process_vapi_event_request({"local_folder": str(ROOT), "event": event})

        self.assertEqual(response["candidate_id"], "candidate-sapna-cv")
        self.assertEqual(response["call_id"], "call-n8n-1")
        self.assertEqual(response["disposition"], "qualified")
        self.assertIn("row", response)

    def test_n8n_workflow_templates_are_importable_json_with_expected_webhooks(self) -> None:
        workflow_dir = ROOT / "n8n" / "workflows"
        workflows = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in workflow_dir.glob("*.json")
        }

        self.assertIn("guidewire-preview.workflow.json", workflows)
        self.assertIn("guidewire-start-calls.workflow.json", workflows)
        self.assertIn("guidewire-vapi-end-call.workflow.json", workflows)
        self.assertIn("role-folder-drive-trigger.workflow.json", workflows)
        preview_paths = _webhook_paths(workflows["guidewire-preview.workflow.json"])
        start_paths = _webhook_paths(workflows["guidewire-start-calls.workflow.json"])
        end_paths = _webhook_paths(workflows["guidewire-vapi-end-call.workflow.json"])
        self.assertIn("guidewire/preview", preview_paths)
        self.assertIn("guidewire/start-calls", start_paths)
        self.assertIn("guidewire/vapi-end", end_paths)
        self.assertTrue(_has_http_node_to(workflows["guidewire-start-calls.workflow.json"], "/start-calls"))
        self.assertTrue(_has_http_node_to(workflows["guidewire-vapi-end-call.workflow.json"], "/vapi/end-of-call"))
        self.assertTrue(_has_http_node_to(workflows["role-folder-drive-trigger.workflow.json"], "/drive-event"))
        self.assertIn("SCREENING_SERVICE_URL", json.dumps(workflows["role-folder-drive-trigger.workflow.json"]))
        self.assertIn("Wait Until Dialing Window", json.dumps(workflows["guidewire-start-calls.workflow.json"]))
        self.assertIn("Wait Until Dialing Window", json.dumps(workflows["role-folder-drive-trigger.workflow.json"]))


def _webhook_paths(workflow: dict) -> set[str]:
    return {
        node["parameters"]["path"]
        for node in workflow["nodes"]
        if node["type"] == "n8n-nodes-base.webhook"
    }


def _has_http_node_to(workflow: dict, path: str) -> bool:
    return any(
        node["type"] == "n8n-nodes-base.httpRequest" and path in json.dumps(node["parameters"])
        for node in workflow["nodes"]
    )


if __name__ == "__main__":
    unittest.main()
