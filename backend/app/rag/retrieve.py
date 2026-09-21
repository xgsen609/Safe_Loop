"""Retrieve only current human-approved procedure chunks by cosine similarity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from app.ai.provider import AIProvider
from app.db import connection
from app.rag.embeddings import embed_texts_batched, vector_literal

DEFAULT_TOP_K = 6
DEFAULT_SIMILARITY_FLOOR = 0.35


@dataclass(frozen=True)
class RetrievedChunk:
    """Return the verbatim content and exact coordinates needed for a citation."""

    content: str
    document_id: UUID
    doc_ref: str
    revision: str
    section: str | None
    page: int | None
    similarity: float


async def retrieve_chunks(
    query: str,
    *,
    provider: AIProvider | None = None,
    top_k: int = DEFAULT_TOP_K,
    similarity_floor: float = DEFAULT_SIMILARITY_FLOOR,
) -> list[RetrievedChunk]:
    """Rank effective approved chunks and discard every result below the floor."""
    clean_query = query.strip()
    if not clean_query:
        return []
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if not 0.0 <= similarity_floor <= 1.0:
        raise ValueError("similarity_floor must be between zero and one")
    query_vector = (
        await embed_texts_batched([clean_query], provider=provider, batch_size=1)
    )[0]
    literal = vector_literal(query_vector)
    async with connection() as conn:
        async with conn.transaction():
            # Safety corpora are deliberately small. Force an exact scan so the
            # top-k membership cannot change at an approximate-index boundary.
            await conn.execute("set local enable_indexscan = off")
            await conn.execute("set local enable_bitmapscan = off")
            rows = await conn.fetch(
                """
                with ranked as materialized (
                  select dc.id as chunk_id,
                         dc.chunk_index,
                         dc.content,
                         d.id as document_id,
                         d.doc_ref,
                         d.revision,
                         dc.section,
                         dc.page,
                         dc.embedding <=> $1::vector(1536) as distance
                  from document_chunks dc
                  join documents d on d.id = dc.document_id
                  where d.is_approved = true
                    and d.effective_from <= now()
                    and dc.embedding is not null
                )
                select content,
                       document_id,
                       doc_ref,
                       revision,
                       section,
                       page,
                       1 - distance as similarity
                from ranked
                where 1 - distance >= $2
                order by distance, doc_ref, revision, chunk_index, chunk_id
                limit $3
                """,
                literal,
                similarity_floor,
                top_k,
            )
    results = [
        RetrievedChunk(
            content=cast(str, row["content"]),
            document_id=cast(UUID, row["document_id"]),
            doc_ref=cast(str, row["doc_ref"]),
            revision=cast(str, row["revision"]),
            section=cast(str | None, row["section"]),
            page=cast(int | None, row["page"]),
            similarity=float(row["similarity"]),
        )
        for row in rows
    ]
    return results
