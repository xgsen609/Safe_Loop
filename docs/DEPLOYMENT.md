# SafeLoop deployment

SafeLoop's production data plane is Singapore-only. Do not substitute a global or US
runtime to get a deployment through.

## Regional topology

| Service | Required target | Repository guard |
| --- | --- | --- |
| Supabase Postgres, Auth and Storage | `ap-southeast-1` (Singapore) | Production DB pooler host and deployment workflow contract |
| Backend and Vertex AI | Cloud Run/Vertex `asia-southeast1` | `GCP_REGION` plus provider endpoint validation |
| Frontend server functions | Vercel `sin1` | `frontend/vercel.json` and workflow contract |
| Browser assets | Vercel global CDN | Static distribution only; application compute remains `sin1` |

The configured Supabase project reference is `ipnyphvlkeezyqdskheo`. Its session
pooler hostname contains `ap-southeast-1`; retain that regional connection string.
The configured Google Cloud project is `safe-506316`, and the backend service name is
`safeloop-api`.

## Deployed demo snapshot

The synthetic demo was deployed and checked on 23 August 2026:

| Surface | Live address | Verified runtime |
| --- | --- | --- |
| Browser | `https://safeloop-ai-demo.vercel.app` | Vercel functions in `sin1` |
| API | `https://safeloop-api-s55z7xniza-as.a.run.app` | Cloud Run `asia-southeast1` |
| Data | `https://ipnyphvlkeezyqdskheo.supabase.co` | Supabase `ap-southeast-1` |

The browser smoke check covered the English and Mandarin sign-in pages and the public
briefing/quiz route. `/health/deep` passed database, private Storage and regional Vertex
provider checks. The hosted records are synthetic and may be reset from
`supabase/demo_seed.sql`.

## Environment contract

Backend secrets belong in Google Secret Manager, never in the container image or Git:

| Secret Manager name | Backend setting |
| --- | --- |
| `safeloop-database-url` | `DATABASE_URL` session-pooler URI |
| `safeloop-jwt-secret` | `SUPABASE_JWT_SECRET` |
| `safeloop-url` | `SUPABASE_URL` |
| `safeloop-service-role-key` | `SUPABASE_SERVICE_ROLE_KEY` |

Cloud Run sets these non-secret values:

```text
APP_ENV=production
ALLOW_DEBUG_AUTH=false
AI_PROVIDER=vertex
VERTEX_PROJECT_ID=safe-506316
VERTEX_LOCATION=asia-southeast1
LIVE_TRANSCRIPTION_ENABLED=true
VERTEX_LIVE_TRANSCRIPTION_MODEL=gemini-3.5-transcribe-live-preview
VERTEX_LIVE_TRANSCRIPTION_LOCATION=global
SITE_TIMEZONE=Asia/Singapore
SUPPORTED_LOCALES=en,zh-CN
FRONTEND_ORIGINS=https://<production-vercel-domain>
```

The Cloud Run runtime service account needs `roles/aiplatform.user` and access only to
the four Secret Manager secrets above. The deployment identity additionally needs
Cloud Run deployment, Service Account User, Cloud Build and Artifact Registry access.
Use GitHub workload identity federation; do not create a long-lived Google service
account key.

Set these Vercel Production environment variables:

| Variable | Value source |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase publishable/anon browser key |
| `BACKEND_URL` | Cloud Run `safeloop-api` URL |
| `NEXT_PUBLIC_BACKEND_URL` | Same Cloud Run URL |
| `NEXT_PUBLIC_BACKEND_WS_URL` | Cloud Run URL with `https://` changed to `wss://` |
| `SITE_TIMEZONE` | `Asia/Singapore` |
| `NEXT_PUBLIC_REPORT_MEDIA_BUCKET` | `report-media` |
| `NEXT_PUBLIC_SITE_EMERGENCY_LINE` | The real site emergency line |

The hosted Singapore demo uses SCDF emergency number `995`. A real site deployment must
replace or confirm that value with its safety officer before production traffic is
enabled. The same approval applies to every Chinese safety term listed in
`docs/GLOSSARY.md`.

## One-time platform setup

1. Confirm the Supabase project region is Singapore and apply the migration history
   baseline only after comparing the live schema with every tracked migration.
2. In `safe-506316`, enable Cloud Run, Cloud Build, Artifact Registry, Secret Manager
   and Vertex AI APIs.
3. Create the runtime and GitHub deployment service accounts with the narrow roles
   described above. Configure workload identity federation for this GitHub repository.
4. Create the four Secret Manager entries and add their values through a protected
   administrator terminal. Do not pass secret values as command arguments.
5. Import `frontend/` into Vercel, retain `sin1`, add the Production variables, and
   obtain a scoped deployment token.
6. Create a protected GitHub environment named `production` and require approval.

Add these GitHub environment secrets:

```text
SUPABASE_DB_URL
GCP_WORKLOAD_IDENTITY_PROVIDER
GCP_DEPLOY_SERVICE_ACCOUNT
VERCEL_TOKEN
VERCEL_ORG_ID
VERCEL_PROJECT_ID
```

Add these GitHub environment variables:

```text
GCP_PROJECT_ID=safe-506316
GCP_RUNTIME_SERVICE_ACCOUNT=<runtime-service-account-email>
FRONTEND_ORIGIN=https://<production-vercel-domain>
```

## Migration and deployment order

Every normal CI run starts a fresh local Supabase service and applies the complete
migration chain before browser tests. Production uses the manually dispatched
`.github/workflows/deploy.yml` workflow:

1. Check the three regional constants and `frontend/vercel.json`.
2. Run `supabase db push` against the protected production database.
3. Build `backend/Dockerfile` and deploy `safeloop-api` to Cloud Run
   `asia-southeast1`.
4. Check `/health` and `/health/deep` on the deployed revision; deep health fails if
   live transcription is disabled, misconfigured, or its shared-state migration is missing.
5. Build the Next.js application with Vercel's Production environment and deploy it
   to `sin1`.
6. Check the English frontend route.

Never load `supabase/demo_seed.sql` into a real production site. It contains public
demo credentials and synthetic operational history. Use a separate demo project.

## Region verification

Run these after every platform move:

```bash
gcloud run services describe safeloop-api \
  --project=safe-506316 \
  --region=asia-southeast1 \
  --format=json | python -c "import json,sys; d=json.load(sys.stdin); print(d['metadata']['labels']['cloud.googleapis.com/location'], d['status']['url'])"

grep 'ap-southeast-1' <<< "$SUPABASE_DB_URL"
pnpm dlx vercel@59.5.0 inspect https://safeloop-ai-demo.vercel.app
```

The Vercel output must identify each server function as `[sin1]`. Also call
`https://safeloop-api-s55z7xniza-as.a.run.app/health/deep`; it confirms database,
private Storage buckets and the configured provider are reachable and intentionally
returns only machine codes.
