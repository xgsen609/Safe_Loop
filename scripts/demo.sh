#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for command_name in python3 npm supabase; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done

if [[ ! -x backend/.venv/bin/python ]]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci --prefer-offline --no-audit

supabase start
supabase db reset --local
eval "$(supabase status -o env)"

export DATABASE_URL="$DB_URL"
export SUPABASE_URL="$API_URL"
export SUPABASE_SERVICE_ROLE_KEY="$SERVICE_ROLE_KEY"
export SUPABASE_JWT_SECRET="${JWT_SECRET:-}"
export NEXT_PUBLIC_SUPABASE_URL="$API_URL"
export NEXT_PUBLIC_SUPABASE_ANON_KEY="$ANON_KEY"
export APP_ENV=local
export ALLOW_DEBUG_AUTH=false
export AI_PROVIDER=stub
export BACKEND_URL=http://127.0.0.1:8000
export NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8000
export SITE_TIMEZONE=Asia/Singapore

backend/.venv/bin/python scripts/apply_demo_seed.py
(cd backend && .venv/bin/python -m app.rag.backfill)
backend/.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 8000 &
backend_pid=$!
trap 'kill "$backend_pid" 2>/dev/null || true' EXIT INT TERM

backend/.venv/bin/python - <<'PY'
import time
from urllib.request import urlopen

for attempt in range(30):
    try:
        with urlopen("http://127.0.0.1:8000/health", timeout=1) as response:
            if response.status == 200:
                break
    except OSError:
        if attempt == 29:
            raise
        time.sleep(1)
PY

echo "SafeLoop demo: http://127.0.0.1:3000/en"
echo "Demo password for every listed account: SafeLoopDemo!2026"
npm --prefix frontend run dev
