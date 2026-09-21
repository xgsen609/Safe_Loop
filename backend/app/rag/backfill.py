"""Backfill missing corpus embeddings without changing document approval state."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

from app.ai.provider import AIProvider
from app.db import close_pool, connection, init_pool
from app.rag.embeddings import embed_texts_batched, vector_literal


async def backfill_missing_embeddings(
    *,
    provider: AIProvider | None = None,
    batch_size: int = 32,
) -> int:
    """Embed every null corpus chunk in a stable order and update atomically."""
    async with connection() as conn:
        rows = await conn.fetch(
            """
            select dc.id, dc.content
            from document_chunks dc
            join documents d on d.id = dc.document_id
            where dc.embedding is null
            order by d.doc_ref, d.revision, dc.chunk_index, dc.id
            """
        )
    if not rows:
        return 0

    chunk_ids = [cast(UUID, row["id"]) for row in rows]
    embeddings = await embed_texts_batched(
        [cast(str, row["content"]) for row in rows],
        provider=provider,
        batch_size=batch_size,
    )
    async with connection() as conn:
        async with conn.transaction():
            await conn.executemany(
                """
                update document_chunks
                set embedding = $2::vector(1536)
                where id = $1 and embedding is null
                """,
                [
                    (chunk_id, vector_literal(embedding))
                    for chunk_id, embedding in zip(
                        chunk_ids,
                        embeddings,
                        strict=True,
                    )
                ],
            )
    return len(chunk_ids)


async def _main() -> None:
    await init_pool()
    try:
        count = await backfill_missing_embeddings()
    finally:
        await close_pool()
    print(f"RAG embeddings ready: {count} chunk(s) backfilled.")


if __name__ == "__main__":
    asyncio.run(_main())
