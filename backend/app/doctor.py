"""Diagnose the local SafeLoop environment and print only the first required fix."""

from __future__ import annotations

import asyncio
import importlib
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from app.config import Settings


def fail(message: str) -> int:
    print(message)
    return 1


async def main() -> int:
    """Run checks in dependency order so each failure has one actionable fix."""
    root = Path.cwd().parent if Path.cwd().name == "backend" else Path.cwd()
    if not (root / "supabase" / "migrations").is_dir():
        return fail("FAIL working directory: run this command from the repository root.")
    try:
        for module in ("fastapi", "asyncpg", "google.genai", "pydantic_settings"):
            importlib.import_module(module)
    except ImportError as error:
        return fail(f"FAIL dependencies: install backend requirements ({error.name}).")
    import asyncpg

    env_file = root / "backend" / ".env"
    if not env_file.is_file() and not os.getenv("DATABASE_URL"):
        return fail("FAIL environment: create backend/.env with DATABASE_URL.")
    try:
        settings = Settings(  # type: ignore[call-arg]
            _env_file=env_file if env_file.is_file() else None
        )
    except Exception:
        return fail("FAIL environment: fix the values in backend/.env.")
    if not settings.database_url:
        return fail("FAIL environment: set DATABASE_URL in backend/.env.")
    parsed = urlparse(settings.database_url)
    host = parsed.hostname
    port = parsed.port or 5432
    if host is None:
        return fail("FAIL database host: set a valid DATABASE_URL.")
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError:
        return fail("FAIL database reachability: start the database or check DATABASE_URL.")
    try:
        conn = await asyncpg.connect(settings.database_url)
    except Exception:
        return fail("FAIL authentication: check the database credentials in backend/.env.")
    try:
        try:
            await conn.fetchval("select 1 from reports limit 1")
        except Exception:
            return fail("FAIL schema: apply the Supabase migrations.")
        try:
            await conn.fetchval("select 1 from audit_log limit 1")
            await conn.fetchval("select 1 from profiles limit 1")
        except Exception:
            return fail("FAIL schema: required SafeLoop tables are missing.")
        try:
            trigger_count = await conn.fetchval(
                """
                select count(*) from pg_trigger
                where not tgisinternal and tgname in (
                  'enforce_status_actor', 'ai_drafts_no_update',
                  'verifications_no_update', 'transcripts_no_update'
                )
                """
            )
            if trigger_count != 4:
                return fail("FAIL guard triggers: apply the database guard migration.")
        except Exception:
            return fail("FAIL guard triggers: inspect the database migration state.")
        try:
            profile_count = await conn.fetchval("select count(*) from profiles")
            if not profile_count:
                return fail("FAIL seeded profiles: run supabase/seed.sql.")
        except Exception:
            return fail("FAIL seeded profiles: run supabase/seed.sql.")
        try:
            missing_embeddings = await conn.fetchval(
                """
                select count(*)
                from document_chunks chunks
                join documents document on document.id = chunks.document_id
                where document.is_approved = true
                  and document.effective_from <= now()
                  and chunks.embedding is null
                """
            )
            if missing_embeddings:
                return fail(
                    "FAIL RAG embeddings: run "
                    "'cd backend && .venv/bin/python -m app.rag.backfill'."
                )
        except Exception:
            return fail("FAIL RAG embeddings: inspect the document corpus schema.")
    finally:
        await conn.close()
    print("PASS SafeLoop environment is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
