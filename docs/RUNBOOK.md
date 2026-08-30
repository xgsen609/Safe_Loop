# SafeLoop production runbook

All production changes require a named operator and a timestamp in the incident or
change record. Never repair workflow state with a direct `UPDATE reports.status`.

## Rollback

### Frontend

1. In Vercel, open Deployments and identify the last known-good Production deployment.
2. Inspect its commit and environment before promoting it.
3. Promote that deployment, then check `/en`, `/zh-CN`, sign-in and one authenticated
   API call.
4. Keep the failed deployment and build logs for diagnosis.

Frontend rollback does not roll back the database or backend. Confirm API compatibility
before promoting an older frontend.

### Backend

List revisions in Singapore:

```bash
gcloud run revisions list \
  --service=safeloop-api \
  --project=safe-506316 \
  --region=asia-southeast1
```

Move all traffic to the last known-good revision:

```bash
gcloud run services update-traffic safeloop-api \
  --project=safe-506316 \
  --region=asia-southeast1 \
  --to-revisions=<known-good-revision>=100
```

Check `/health`, `/health/deep`, one report read and one role-scoped queue read. Do not
delete the failed revision until its logs and request IDs have been retained.

### Database

Migrations are forward-only by default. If an application rollback remains compatible,
leave the schema in place. For an incompatible or destructive migration:

1. Stop deployment and write access.
2. Preserve audit, append-only AI draft, verification and receipt tables.
3. Restore a Supabase point-in-time recovery branch or backup to a separate project.
4. Verify row counts, enum labels, RLS policies and guard triggers there.
5. Switch traffic only after the application suite passes against the restored copy.

Never edit an applied migration file and never delete evidence rows to make rollback
appear successful.

## Key rotation

Rotate one credential at a time and keep the old value active until the new revision is
healthy.

### Supabase database password

1. Rotate the password in Supabase and obtain a Singapore session-pooler URI.
2. Add a new version to `safeloop-database-url` and update `SUPABASE_DB_URL` in the
   protected GitHub environment.
3. Deploy the backend, check deep health, then revoke the old password.

### Supabase service-role and browser keys

1. Create/rotate the key in Supabase API Keys.
2. Put the service-role value in a new `safeloop-service-role-key` version.
3. Update only the publishable/anon value in Vercel.
4. Deploy both surfaces, test signed media/document access, then revoke the old keys.

Never expose the service-role key to a `NEXT_PUBLIC_*` variable.

### JWT secret

The current backend contract verifies HS256 tokens with `SUPABASE_JWT_SECRET`. Rotating
it invalidates outstanding sessions. Schedule a sign-out window, create the new Secret
Manager version, deploy the backend and require users to sign in again. Verify reporter,
reviewer and responsible-role tokens before completing the change.

### Google and Vercel deployment access

Prefer GitHub OIDC for Google; if federation is compromised, disable the provider or
attribute mapping, create a replacement, update the protected GitHub environment and
review Cloud Audit Logs. Revoke and replace the scoped Vercel token, then run a manual
deployment. Neither token belongs in repository variables or local `.env` files.

## AI provider outage

The intake circuit breaker fails closed. A failed graph leaves the report in
`submitted`; it does not create a draft or claim that a human reviewed it. Urgent alerts
remain independent and must continue to reach reviewers.

1. Confirm `/health/deep` reports `provider_unreachable` and use request IDs to inspect
   `ai_run_failed` logs. Check Vertex AI status and quota in `asia-southeast1`.
2. Do not switch production to the stub provider. The stub is only for deterministic
   tests and demos.

## Gemini Live transcription outage

Voice must degrade to recorded-audio transcription and then to typing; it must never
block filing a report.

1. Check `/health/deep` for `live_transcription_disabled`,
   `live_transcription_misconfigured`, or `database_schema_missing`.
2. Confirm Cloud Run has `LIVE_TRANSCRIPTION_ENABLED=true`, the preview model name,
   and `VERTEX_LIVE_TRANSCRIPTION_LOCATION=global`. Confirm the Vercel build used the
   Cloud Run `wss://` URL.
3. Inspect structured failures for invalid tickets, provider unavailability, and
   missing committed sessions. Do not rely on Cloud Run session affinity: tickets and
   pending results must remain in the shared Postgres tables.
4. If Gemini Live is unavailable, leave the fallback enabled. The reporter can stop
   recording, wait for uploaded-audio transcription, edit the result, or type instead.
3. Tell reviewers to monitor submitted reports and urgent alerts. Do not directly move
   a report into review or closure.
4. After Vertex recovers, retry each still-submitted report through the existing service:

   ```bash
   python -c "import asyncio; from uuid import UUID; from app.services.intake_service import run_intake; asyncio.run(run_intake(UUID('<report-id>'), 'provider-recovery'))"
   ```

5. Confirm each retry either enters clarification, produces a validated review draft,
   or remains submitted with a new diagnosable failure. Record every affected report ID.

Do not rerun a report that has already advanced; `run_intake` refuses statuses outside
`submitted` and answer-complete `clarifying`.

### Gemini speech transcription unavailable

Voice is an optional input aid, never a filing dependency. When `/transcribe` returns a
provider failure or the circuit is open, the current voice control hides, the editable text
field remains available, and report, clarification, and completion-note submission continue
with typed text. The server records the failed attempt for the dashboard; no report text is
created from a failed transcript and no report is lost.

1. Confirm the synchronous Vertex model is available in `asia-southeast1`. When
   `LIVE_TRANSCRIPTION_ENABLED=true`, also check the explicitly approved global Agent Platform
   preview `gemini-3.5-transcribe-live-preview`. This is a documented residency exception, not
   permission to use the public Generative Language endpoint.
2. Check `/health/deep`, `provider_unavailable`/`circuit_open` responses, quota, and the
   dashboard failure rate by locale. Do not enable the stub in production.
3. Tell users to type in the still-editable field. Do not retry or submit on their behalf.
4. After recovery, make one short English and one short Mandarin recording and confirm the
   transcript appears in the editable field before declaring recovery.

After any Gemini model-version change, run the non-CI corpus from `backend/` with production-
equivalent project and region configuration:

```bash
python -m app.ai.eval_asr
python -m app.ai.eval_asr --provider live
```

Record the model version and per-language CER/WER. Investigate clean Mandarin CER above 15%
or noisy Mandarin CER above 30%; compare English, Mandarin, code-switched, and noisy fixtures
before promoting the model. `python -m app.ai.eval_asr --provider stub` checks only harness
plumbing and is not an accuracy result.

The Live preview currently requires both `cmn-Hans-CN` and `en-GB` in `language_codes` to
preserve SafeLoop code-switching. It may omit a language code and confidence; SafeLoop then
infers `cmn-Hans-CN`, `en`, or `mul` from the returned script and stores confidence as null.
Never invent a numerical confidence value. If the WebSocket fails, the browser retries the
stored clip through synchronous `/transcribe`; typing remains the final fallback.

## First-response checks

| Symptom | First check |
| --- | --- |
| API unavailable | Cloud Run revision health, traffic split and request logs |
| Sign-in succeeds but API returns 401 | JWT secret/audience and backend deployment time |
| Photos or documents fail | Deep-health Storage code, bucket privacy and service-role key |
| Queue is empty for one role | Profile role, RLS policy and active assignment—not a direct data update |
| Urgent banner is absent | Alert row, reviewer/admin profile and polling request logs |
| Reports remain submitted | Provider deep health, circuit state and `ai_run_failed` logs |
| Quiz returns 429 | IP rate-limit window and request ID; do not delete valid responses |

The safety invariant takes priority over availability: there is no machine fallback to
verified closure.
