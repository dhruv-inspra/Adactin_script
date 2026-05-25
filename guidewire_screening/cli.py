from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .google_auth import token_provider_from_env
from .google_drive import DRIVE_SCOPE, GoogleDriveRoleFolder
from .parsers import parse_candidates_from_folder, parse_jd, parse_questionnaire
from .pipeline import build_call_inputs, find_role_files, load_local_role_folder
from .qualification import evaluate_candidate
from .vapi import build_vapi_call_payload, place_vapi_call
from .webhook import serve_webhook
from .api import serve_api


def main() -> int:
    parser = argparse.ArgumentParser(description="Guidewire screening voice-agent demo workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dry_run = subparsers.add_parser("dry-run", help="Preview parsed candidates and Vapi call payload metadata")
    _add_source_args(dry_run)
    dry_run.add_argument("--out", type=Path, default=Path("outputs/dry_run.json"))

    call = subparsers.add_parser("call", help="Trigger outbound Vapi calls")
    _add_source_args(call)
    call.add_argument("--phone-number-id", default=os.environ.get("VAPI_PHONE_NUMBER_ID"))
    call.add_argument("--assistant-id", default=os.environ.get("VAPI_ASSISTANT_ID"))
    call.add_argument("--api-key", default=os.environ.get("VAPI_API_KEY"))
    call.add_argument("--server-url", default=os.environ.get("VAPI_SERVER_URL"))
    call.add_argument("--execute", action="store_true", help="Actually place calls. Without this, only writes payloads.")
    call.add_argument("--limit", type=int)
    call.add_argument("--out", type=Path, default=Path("outputs/vapi_payloads.json"))

    webhook = subparsers.add_parser("serve-webhook", help="Receive Vapi end-of-call reports and write result rows")
    _add_source_args(webhook)
    webhook.add_argument("--host", default="0.0.0.0")
    webhook.add_argument("--port", type=int, default=4242)
    webhook.add_argument("--results-csv", type=Path, default=Path("outputs/results.csv"))
    webhook.add_argument("--google-spreadsheet-id", default=os.environ.get("GOOGLE_SPREADSHEET_ID"))

    api = subparsers.add_parser("serve-api", help="Serve HTTP endpoints for n8n orchestration")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=4242)

    args = parser.parse_args()
    if args.command == "dry-run":
        return run_dry_run(args)
    if args.command == "call":
        return run_call(args)
    if args.command == "serve-webhook":
        return run_serve_webhook(args)
    if args.command == "serve-api":
        serve_api(host=args.host, port=args.port)
        return 0
    return 1


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--local-folder", type=Path)
    source.add_argument("--drive-folder-id")


def load_source(args: argparse.Namespace) -> tuple:
    if args.local_folder:
        return load_local_role_folder(args.local_folder)

    provider = token_provider_from_env([DRIVE_SCOPE])
    drive = GoogleDriveRoleFolder(provider)
    folder = drive.download_folder(args.drive_folder_id, Path("outputs") / "drive_cache" / args.drive_folder_id)
    jd_path, questionnaire_path = find_role_files(folder)
    return parse_jd(jd_path), parse_questionnaire(questionnaire_path), parse_candidates_from_folder(folder)


def run_dry_run(args: argparse.Namespace) -> int:
    jd, questions, candidates = load_source(args)
    call_inputs = build_call_inputs(candidates, jd, questions)
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    payload = {
        "role_title": jd.title,
        "question_count": len(questions),
        "candidate_count": len(candidates),
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "name": item.name_if_known,
                "phone": item.phone,
                "question_count": len(item.questions),
                "cv_summary": item.cv_summary,
                "parsed_cv_facts": candidates_by_id[item.candidate_id].screening_facts,
                "cv_predicted_disposition": evaluate_candidate(
                    questions, candidates_by_id[item.candidate_id].screening_facts
                ).disposition,
            }
            for item in call_inputs
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def run_call(args: argparse.Namespace) -> int:
    if not args.phone_number_id:
        raise SystemExit("Set --phone-number-id or VAPI_PHONE_NUMBER_ID.")
    if args.execute and not args.api_key:
        raise SystemExit("Set --api-key or VAPI_API_KEY when using --execute.")

    jd, questions, candidates = load_source(args)
    call_inputs = build_call_inputs(candidates, jd, questions)
    if args.limit:
        call_inputs = call_inputs[: args.limit]

    payloads = [
        build_vapi_call_payload(
            item,
            phone_number_id=args.phone_number_id,
            server_url=args.server_url,
            assistant_id=args.assistant_id,
        )
        for item in call_inputs
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payloads, indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.execute:
        print(f"Wrote {len(payloads)} payloads to {args.out}. Re-run with --execute to place calls.")
        return 0

    responses = [place_vapi_call(payload, args.api_key) for payload in payloads]
    response_path = args.out.with_name(args.out.stem + "_responses.json")
    response_path.write_text(json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Placed {len(responses)} Vapi calls. Responses written to {response_path}.")
    return 0


def run_serve_webhook(args: argparse.Namespace) -> int:
    _jd, questions, candidates = load_source(args)
    serve_webhook(
        host=args.host,
        port=args.port,
        candidates_by_id={candidate.candidate_id: candidate for candidate in candidates},
        questions=questions,
        results_csv=args.results_csv,
        google_spreadsheet_id=args.google_spreadsheet_id,
    )
    return 0


def evaluate_answers(questionnaire: Path, answers_json: Path) -> int:
    questions = parse_questionnaire(questionnaire)
    answers = json.loads(answers_json.read_text(encoding="utf-8"))
    result = evaluate_candidate(questions, answers)
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
