# Role Folder Screening Voice Agent Demo

Python orchestration for Adactin role-based screening calls.

The workflow reads a role folder containing:

- one JD `.docx`
- one questionnaire `.xlsx`
- candidate CVs as `.docx` or `.pdf`

It parses candidates, derives JD-specific interview questions, builds Vapi outbound-call payloads, receives end-of-call reports, evaluates qualifying answers, and writes recruiter-facing result rows.

## What Is Implemented

- CV parsing for phone numbers, names, email addresses, summaries, and screening-summary facts.
- JD parsing for role title and required-role context.
- Questionnaire parsing from `Questions Template.xlsx`; the workbook remains the base questionnaire.
- JD-derived role questions are appended to the base questionnaire for each role folder.
- Monica prompt generation with the revised voice style, consent flow, guardrails, and no Zoho tagging tool.
- Vapi outbound call payload generation using either a saved assistant plus the `role_title` and `qualification_questions` dynamic variables, or a transient assistant config when no assistant id is provided.
- Manual dry-run and call-trigger CLI.
- HTTP API endpoints for n8n orchestration.
- Importable n8n workflow templates for preview, starting calls, and Vapi end-of-call reports.
- Importable n8n Google Drive trigger workflow for new role folders and uploaded resume/JD files.
- Outbound calls can be started immediately when execution is enabled.
- Execution is gated to the Melbourne dialing window, Monday to Friday from 9 AM to 6 PM. Outside that window, call payloads are queued instead of dialed.
- End-of-call webhook server that evaluates answers and appends results to CSV and optionally Google Sheets.
- Service-account Google Drive folder download and Google Sheets append adapters.

## Setup

Use the bundled Python runtime in Codex, or install the dependencies in your own environment:

```bash
python3 -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` in your deployment environment and set real values there.

For Google Drive and Sheets, create a Google Cloud service account, download its JSON key, and share the Drive role folder and output Sheet with the service account email.

## Local Demo

Preview the current sample folder without calling anyone:

```bash
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m guidewire_screening.cli dry-run \
  --local-folder . \
  --out outputs/dry_run.json
```

Build one Vapi payload without placing a call:

```bash
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m guidewire_screening.cli call \
  --local-folder . \
  --phone-number-id "$VAPI_PHONE_NUMBER_ID" \
  --assistant-id "$VAPI_ASSISTANT_ID" \
  --server-url "$VAPI_SERVER_URL" \
  --limit 1 \
  --out outputs/vapi_payloads.json
```

Place real calls only after reviewing the payloads:

```bash
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m guidewire_screening.cli call \
  --local-folder . \
  --phone-number-id "$VAPI_PHONE_NUMBER_ID" \
  --assistant-id "$VAPI_ASSISTANT_ID" \
  --server-url "$VAPI_SERVER_URL" \
  --execute
```

## Google Drive Input

Use a shared Google Drive folder as the role source:

```bash
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json \
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m guidewire_screening.cli dry-run \
  --drive-folder-id "$GOOGLE_DRIVE_FOLDER_ID"
```

The folder should contain the same file types as the local sample folder.

## Vapi Webhook

Start the webhook receiver:

```bash
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m guidewire_screening.cli serve-webhook \
  --local-folder . \
  --host 0.0.0.0 \
  --port 4242 \
  --results-csv outputs/results.csv
```

Expose it publicly for Vapi using a tunnel or deployment platform, then set `VAPI_SERVER_URL` to the public `/webhook` URL.

To append to Google Sheets as well:

```bash
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json \
GOOGLE_SPREADSHEET_ID=your-sheet-id \
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m guidewire_screening.cli serve-webhook \
  --local-folder . \
  --results-csv outputs/results.csv
```

Create a `Results` tab in the target Google Sheet. The sink appends columns in the order defined by `guidewire_screening.results.RESULT_COLUMNS`.

## n8n Automation

Recommended production split:

- n8n controls triggers, inspection, retries, and routing.
- Python keeps parsing, prompt generation, Vapi payload generation, and qualification logic.
- Vapi places calls and sends end-of-call reports back to n8n.
- Google Sheets remains the recruiting team result surface.

Start the Python API that n8n will call:

```bash
SCREENING_LOCAL_FOLDER="/Users/mc/Downloads/Guidewire developer Role" \
VAPI_PHONE_NUMBER_ID="$VAPI_PHONE_NUMBER_ID" \
VAPI_API_KEY="$VAPI_API_KEY" \
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m guidewire_screening.cli serve-api \
  --host 0.0.0.0 \
  --port 4242
```

Import these files into n8n using **Import from File**:

- `n8n/workflows/guidewire-preview.workflow.json`
- `n8n/workflows/guidewire-start-calls.workflow.json`
- `n8n/workflows/guidewire-vapi-end-call.workflow.json`
- `n8n/workflows/role-folder-drive-trigger.workflow.json`

n8n workflow import/export is JSON-based, and n8n supports importing workflow JSON from file or URL. See n8n's official import/export docs: <https://docs.n8n.io/workflows/export-import/>.

### Preview Workflow

POST to the n8n `guidewire/preview` webhook:

```json
{
  "screening_api_base_url": "http://host.docker.internal:4242",
  "local_folder": "/Users/mc/Downloads/Guidewire developer Role"
}
```

For Drive input, use:

```json
{
  "screening_api_base_url": "http://host.docker.internal:4242",
  "drive_folder_id": "your-google-drive-folder-id"
}
```

### Start Calls Workflow

First run it with `execute: false` to inspect payloads in n8n:

```json
{
  "screening_api_base_url": "http://host.docker.internal:4242",
  "local_folder": "/Users/mc/Downloads/Guidewire developer Role",
  "phone_number_id": "your-vapi-phone-number-id",
  "server_url": "https://your-n8n-domain/webhook/guidewire/vapi-end",
  "limit": 1,
  "execute": false
}
```

Set `execute` to `true` only when ready to place outbound calls.

When `execute` is `true`, Python starts Vapi calls immediately.

If the workflow is triggered by one changed resume, pass either `candidate_file_id`, `candidate_file_name`, or `candidate_id`. The API will scan the role folder but dial only that candidate:

```json
{
  "screening_api_base_url": "http://host.docker.internal:4242",
  "drive_folder_id": "role-folder-id",
  "candidate_file_id": "changed-resume-drive-file-id",
  "phone_number_id": "your-vapi-phone-number-id",
  "server_url": "https://your-n8n-domain/webhook/guidewire/vapi-end",
  "execute": false
}
```

### Role Folder Drive Trigger Workflow

`role-folder-drive-trigger.workflow.json` is the production-style trigger setup:

- Google Drive trigger watches the parent folder where role folders are created.
- If a new folder is created, it treats that folder as the role folder.
- If a file is uploaded inside an existing role folder, it uses the file's parent as the role folder.
- It waits 2 minutes before scanning so resume/JD uploads can finish.
- It posts the Drive event to the deployable Python service at `/drive-event`.
- The Python service downloads the role folder, parses the JD and resumes, builds role questions, and sends `role_title` and `qualification_questions` to Vapi.

Set these n8n environment variables:

```text
ROLE_PARENT_FOLDER_ID=Google Drive folder that contains all role folders
SCREENING_SERVICE_URL=https://your-render-service.onrender.com
VAPI_EXECUTE_CALLS=false
```

Keep `VAPI_EXECUTE_CALLS=false` until the preview payloads look correct. Set it to `true` only when you want real outbound calls.

The Drive trigger workflow calls the Python service immediately after the upload wait step.

Put the prompt from `vapi_prompt_with_variable.md` in the saved Vapi assistant and include `{{role_title}}` where the role should appear and `{{qualification_questions}}` where the questions should appear.

The service deploy files are in `deployable_python_service/`.

### Vapi End-Call Workflow

Set the Vapi assistant server URL to the production n8n webhook URL for `guidewire/vapi-end`.

The template forwards the report to:

```text
POST /vapi/end-of-call
```

The Python API evaluates disposition and returns a row-ready result object to n8n. If you include `results_csv` or `google_spreadsheet_id`, the API also writes the result.

The n8n templates use Webhook, HTTP Request, Respond to Webhook, and one Code node. These are core n8n nodes documented here:

- Webhook: <https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/>
- HTTP Request: <https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/>
- Code node: <https://docs.n8n.io/code/code-node/>

## Verification

Run the automated checks:

```bash
/Users/mc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m unittest discover -s tests
```

The tests cover sample CV parsing, questionnaire parsing, prompt cleanup, qualification rules, Vapi payload shape, webhook result conversion, and result export.
#   A d a c t i n _ s c r i p t  
 
