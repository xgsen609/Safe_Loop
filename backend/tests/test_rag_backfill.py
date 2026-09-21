"""Keep restored and demo corpora retrievable after deterministic seeding."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

import pytest

from app.ai.provider import EMBEDDING_DIMENSIONS, Vector
from app.rag import backfill


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.rows = [
            {
                "id": UUID("62100000-0000-4000-8000-000000000001"),
                "content": "Install a secured guardrail before work resumes.",
            },
            {
                "id": UUID("62100000-0000-4000-8000-000000000002"),
                "content": "Keep scaffold access gates self-closing.",
            },
        ]
        self.updates: list[tuple[UUID, str]] = []

    async def fetch(self, query: str) -> list[dict[str, object]]:
        assert "where dc.embedding is null" in query
        assert "order by d.doc_ref, d.revision, dc.chunk_index, dc.id" in query
        return self.rows

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def executemany(
        self,
        query: str,
        values: list[tuple[UUID, str]],
    ) -> None:
        assert "where id = $1 and embedding is null" in query
        self.updates.extend(values)


def test_missing_embeddings_are_backfilled_in_one_atomic_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeConnection()

    @asynccontextmanager
    async def fake_connection() -> AsyncIterator[FakeConnection]:
        yield fake

    async def fake_embed(
        texts: list[str],
        **_kwargs: object,
    ) -> list[Vector]:
        assert texts == [row["content"] for row in fake.rows]
        return [[float(index + 1)] * EMBEDDING_DIMENSIONS for index in range(2)]

    monkeypatch.setattr(backfill, "connection", fake_connection)
    monkeypatch.setattr(backfill, "embed_texts_batched", fake_embed)

    count = asyncio.run(backfill.backfill_missing_embeddings())

    assert count == 2
    assert [chunk_id for chunk_id, _ in fake.updates] == [
        row["id"] for row in fake.rows
    ]
    assert all(vector.startswith("[") and vector.endswith("]") for _, vector in fake.updates)
