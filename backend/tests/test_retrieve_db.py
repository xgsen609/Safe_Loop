"""Prove pgvector retrieval cannot cross the human approval boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterator
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar
from uuid import UUID, uuid4

import pytest

from app.db import close_pool, connection, init_pool
from app.domain.enums import ActorType, Role
from app.rag.chunker import DOCX_MIME_TYPE, PDF_MIME_TYPE
from app.rag.retrieve import retrieve_chunks
from app.services.document_service import (
    approve_document,
    ingest_document,
    retire_document,
)
from app.services.report_service import Actor

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")
FIXTURES = Path(__file__).parent / "fixtures"
REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000003")
T = TypeVar("T")
_test_loop: asyncio.AbstractEventLoop | None = None


@pytest.fixture(scope="module", autouse=True)
def database_pool() -> Iterator[None]:
    global _test_loop
    assert DATABASE_URL is not None
    _test_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_test_loop)
    _test_loop.run_until_complete(init_pool(DATABASE_URL))
    yield
    _test_loop.run_until_complete(close_pool())
    _test_loop.close()
    _test_loop = None
    asyncio.set_event_loop(None)


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    assert _test_loop is not None
    return _test_loop.run_until_complete(coroutine)


def reviewer() -> Actor:
    return Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER)


async def no_storage(_path: str, _content: bytes, _mime_type: str) -> None:
    return None


async def cleanup(prefix: str) -> None:
    async with connection() as conn:
        await conn.execute("delete from documents where doc_ref like $1", f"{prefix}%")


def test_pgvector_column_and_cosine_index_are_present() -> None:
    async def inspect_schema() -> tuple[str, bool]:
        async with connection() as conn:
            row = await conn.fetchrow(
                """
                select format_type(attribute.atttypid, attribute.atttypmod) as embedding_type,
                       exists (
                         select 1
                         from pg_index index_record
                         join pg_class index_class on index_class.oid = index_record.indexrelid
                         where index_class.relname = 'document_chunks_embedding_cosine_ivfflat'
                           and index_record.indisvalid
                       ) as index_exists
                from pg_attribute attribute
                where attribute.attrelid = 'document_chunks'::regclass
                  and attribute.attname = 'embedding'
                  and not attribute.attisdropped
                """
            )
        assert row is not None
        return str(row["embedding_type"]), bool(row["index_exists"])

    assert run(inspect_schema()) == ("vector(1536)", True)


def test_retrieval_is_ranked_approved_effective_and_fast() -> None:
    prefix = f"TEST-RETRIEVE-{uuid4()}"
    effective = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    pdf = (FIXTURES / "english-procedure.pdf").read_bytes()
    docx = (FIXTURES / "zh-CN-procedure.docx").read_bytes()
    try:
        chinese = run(
            ingest_document(
                reviewer(),
                title="中文程序",
                doc_ref=f"{prefix}-ZH",
                revision="1",
                effective_from=effective,
                filename="procedure.docx",
                claimed_mime_type=DOCX_MIME_TYPE,
                content=docx,
                storage_uploader=no_storage,
            )
        )
        run(approve_document(chinese["id"], reviewer()))

        unapproved = run(
            ingest_document(
                reviewer(),
                title="Pending duplicate",
                doc_ref=f"{prefix}-PENDING",
                revision="1",
                effective_from=effective,
                filename="procedure.docx",
                claimed_mime_type=DOCX_MIME_TYPE,
                content=docx,
                storage_uploader=no_storage,
            )
        )
        future_document = run(
            ingest_document(
                reviewer(),
                title="Future duplicate",
                doc_ref=f"{prefix}-FUTURE",
                revision="1",
                effective_from=future,
                filename="procedure.docx",
                claimed_mime_type=DOCX_MIME_TYPE,
                content=docx,
                storage_uploader=no_storage,
            )
        )
        run(approve_document(future_document["id"], reviewer()))

        old_revision = run(
            ingest_document(
                reviewer(),
                title="Old English procedure",
                doc_ref=f"{prefix}-EN",
                revision="1",
                effective_from=effective,
                filename="procedure.pdf",
                claimed_mime_type=PDF_MIME_TYPE,
                content=pdf,
                storage_uploader=no_storage,
            )
        )
        run(approve_document(old_revision["id"], reviewer()))
        run(retire_document(old_revision["id"], reviewer()))
        current_revision = run(
            ingest_document(
                reviewer(),
                title="Current English procedure",
                doc_ref=f"{prefix}-EN",
                revision="2",
                effective_from=effective,
                filename="procedure.pdf",
                claimed_mime_type=PDF_MIME_TYPE,
                content=pdf,
                storage_uploader=no_storage,
            )
        )
        run(approve_document(current_revision["id"], reviewer()))

        run(retrieve_chunks("开始工作前必须安装护栏"))
        started = perf_counter()
        mandarin_results = run(retrieve_chunks("开始工作前必须安装护栏"))
        repeated_results = run(retrieve_chunks("开始工作前必须安装护栏"))
        elapsed = perf_counter() - started
        returned_ids = {result.document_id for result in mandarin_results}

        assert chinese["id"] in returned_ids
        assert unapproved["id"] not in returned_ids
        assert future_document["id"] not in returned_ids
        assert all(result.similarity >= 0.35 for result in mandarin_results)
        assert repeated_results == mandarin_results
        assert mandarin_results == sorted(
            mandarin_results,
            key=lambda result: result.similarity,
            reverse=True,
        )
        assert elapsed < 0.2

        english_results = run(
            retrieve_chunks("Stop work when fall protection is missing")
        )
        english_ids = {result.document_id for result in english_results}
        assert current_revision["id"] in english_ids
        assert old_revision["id"] not in english_ids

        assert run(retrieve_chunks("食堂菜单和办公室文具清单")) == []
    finally:
        run(cleanup(prefix))
