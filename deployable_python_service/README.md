# Deployable Role Folder Screening Service

This FastAPI service replaces the n8n processing steps. n8n only forwards a Google Drive event to `/drive-event`; this service downloads the role folder, parses the JD and resumes, builds qualification questions, sends `role_title` and `qualification_questions` to Vapi as dynamic variables, and optionally starts calls.

## Recommended Host

Use Render as a Python Web Service. This workflow is better as a normal HTTP service than a serverless function because it downloads Drive files, parses documents, and may place multiple outbound calls.

## Render Settings

Deploy from the repository root, not from this subfolder.

Build command:

```bash
pip install -r deployable_python_service/requirements.txt
```

Start command:

```bash
uvicorn deployable_python_service.app:app --host 0.0.0.0 --port $PORT
```

Environment variables:

```text
GOOGLE_SERVICE_ACCOUNT_JSON_B64=base64 encoded service account JSON
VAPI_PHONE_NUMBER_ID=your Vapi phone number id
VAPI_ASSISTANT_ID=your saved Vapi assistant id
VAPI_API_KEY=your Vapi API key
VAPI_SERVER_URL=https://your-render-service.onrender.com/vapi/end-of-call
VAPI_EXECUTE_CALLS=false
```

Keep `VAPI_EXECUTE_CALLS=false` until the returned Vapi payloads look correct. Set it to `true` when you want real calls.

When execution is enabled, calls are placed only during the dialing window `Mon-Fri 09:00-18:00` in `Australia/Melbourne`. Outside that window, the service returns `status: "calls_queued"` and writes payloads to `CALL_QUEUE_FILE` or `outputs/call_queue.jsonl`.

## Endpoints

`GET /health`

Checks the service is running.

`POST /drive-event`

Main endpoint for n8n. Send the Google Drive trigger payload. The service resolves the role folder like this:

```text
If the event is a folder, use that folder id.
If the event is a file, use the first parent folder id.
If drive_folder_id is provided directly, use that.
```

`POST /preview-folder`

Manual test endpoint:

```json
{
  "drive_folder_id": "google-drive-role-folder-id",
  "limit": 1
}
```

`POST /vapi/end-of-call`

Webhook endpoint for Vapi end-of-call reports. The service reads `drive_folder_id` from Vapi call metadata.

## How It Works

1. n8n receives a Google Drive file/folder created event.
2. n8n posts that event to `https://your-render-service.onrender.com/drive-event`.
3. The service downloads supported files from the role folder: `.docx`, `.pdf`, `.xlsx`, Google Docs, and Google Sheets.
4. It finds the JD, parses resumes, extracts candidate name and phone number, and builds questions.
5. If an `.xlsx` questionnaire exists, those questions are used first. If not, the service uses a default basic qualification set.
6. JD-based role questions are appended.
7. The service creates Vapi call payloads using the saved assistant id and these dynamic variables:

```json
{
  "assistantOverrides": {
    "variableValues": {
      "role_title": "Guidewire Developer",
      "qualification_questions": "q001: ...\nq002: ..."
    }
  }
}
```

8. If `VAPI_EXECUTE_CALLS=false`, it returns payloads only. If `true`, it calls Vapi immediately.

## Google Drive Access

Share the parent Drive folder, or each role folder, with the service account email. The service account must have read access.

To create `GOOGLE_SERVICE_ACCOUNT_JSON_B64` on PowerShell:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json"))
```
