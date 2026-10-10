# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""pgvector vector-store adapter — infrastructure layer for the Wildlife Brain.

DINOv3 vectors live in Postgres as ``media_embeddings.embedding`` (pgvector),
beside the relational data — so they inherit RLS + PITR and need no separate
service. This replaces the former Qdrant adapter; it keeps the same method
surface (``upsert`` / ``search`` / ``retrieve_vector`` / …) so callers in
``domain/wildlife_brain.py`` and ``routers/brain.py`` are unchanged.

Design (per the backend agent skill — services do infra only, no business logic,
no FastAPI):
- The vector column is unbounded, so one column holds both variants (384-d
  ViT-S / 1280-d ViT-H). Every read filters by ``embedding_model`` so compared
  vectors share a dim — enforced here (each service instance is bound to one
  model) and in the ``match_media_embeddings`` SQL function.
- Similarity search runs through that function (cosine ``<=>``); everything else
  is plain Supabase table access via the service-role client.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import structlog
from postgrest import CountMethod

from app.registries.embedding_registry import (
    DEFAULT_SERVER_MODEL,
    EMBEDDING_DIM,
    get_embedding_dim,
)
from app.services.db_utils import row_of, rows_of
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()

_TABLE = "media_embeddings"
_MATCH_FN = "match_media_embeddings"


# ── Pure helpers (no DB — unit-testable) ─────────────────────────────


@dataclass(frozen=True)
class SimilarPoint:
    """A single nearest-neighbour result (id + similarity score + payload)."""

    id: str
    score: float
    payload: dict[str, Any]


def format_vector(vector: Sequence[float]) -> str:
    """Render a vector as the pgvector text literal ``[x,y,z]`` (PostgREST casts it)."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def parse_vector(value: Any) -> Optional[list[float]]:
    """Parse a pgvector value coming back from PostgREST (a ``"[x,y]"`` string)."""
    if value is None:
        return None
    if isinstance(value, list):
        return [float(x) for x in value]
    return [float(x) for x in json.loads(value)]


def validate_vectors(vectors: Sequence[Sequence[float]], expected: int) -> None:
    """Ensure every vector has the expected dimensionality for its model."""
    for i, v in enumerate(vectors):
        if len(v) != expected:
            raise ValueError(f"Embedding dim mismatch at index {i}: got {len(v)}, expected {expected}")


def build_payload(
    *,
    deployment_id: str,
    embedding_run_id: str,
    org_id: Optional[str] = None,
    taxon_id: Optional[str] = None,
    cluster_id: Optional[int] = None,
    review_status: Optional[str] = None,
) -> dict[str, Any]:
    """Canonical point payload — kept for call-site compatibility with the old Qdrant
    adapter. Only ``deployment_id`` / ``embedding_run_id`` are persisted on the vector
    row; the rest are already relational columns on ``media_embeddings``.
    """
    payload: dict[str, Any] = {"deployment_id": deployment_id, "embedding_run_id": embedding_run_id}
    for k, v in (("org_id", org_id), ("taxon_id", taxon_id), ("cluster_id", cluster_id), ("review_status", review_status)):
        if v is not None:
            payload[k] = v
    return payload


# ── Service ──────────────────────────────────────────────────────────


class PgVectorService:
    """Async adapter over ``media_embeddings.embedding`` (pgvector) for one model variant."""

    def __init__(self, model_name: Optional[str] = None, dim: Optional[int] = None) -> None:
        self.model_name = model_name or DEFAULT_SERVER_MODEL.value
        self.dim = dim or (get_embedding_dim(self.model_name) if model_name else EMBEDDING_DIM)

    async def ensure_collection(self) -> bool:
        """No-op — the table + index exist via the ww-backend migration."""
        return False

    async def upsert(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict[str, Any]],
    ) -> int:
        """Write vectors onto their ``media_embeddings`` rows. Returns the count written.

        Merges by ``media_id``; the clustering/UMAP columns written separately by
        ``wildlife_brain`` are preserved (PostgREST upsert only SETs provided columns).
        """
        if not (len(ids) == len(vectors) == len(payloads)):
            raise ValueError(f"ids/vectors/payloads length mismatch: {len(ids)}/{len(vectors)}/{len(payloads)}")
        validate_vectors(vectors, self.dim)
        if not ids:
            return 0

        rows = [
            {
                "media_id": mid,
                "deployment_id": payload["deployment_id"],
                "embedding_run_id": payload.get("embedding_run_id"),
                "embedding": format_vector(vec),
                "embedding_model": self.model_name,
            }
            for mid, vec, payload in zip(ids, vectors, payloads)
        ]

        def _run() -> int:
            client = create_service_client()
            for i in range(0, len(rows), 100):
                client.table(_TABLE).upsert(rows[i : i + 100], on_conflict="media_id").execute()
            return len(rows)

        n = await asyncio.to_thread(_run)
        logger.debug("pgvector_upserted", count=n, model=self.model_name)
        return n

    async def search(
        self,
        vector: Sequence[float],
        limit: int = 20,
        conditions: Optional[dict[str, Any]] = None,
        exclude_media_id: Optional[str] = None,
    ) -> list[SimilarPoint]:
        """Cosine nearest-neighbour search via ``match_media_embeddings``.

        ``conditions['deployment_id']`` (scalar or list) scopes the search; other
        keys are ignored (the row's model already fixes the vector space).
        ``exclude_media_id`` drops the query media itself at the DB level (for
        "find similar to X"). Score is cosine similarity (1 − distance), matching
        the old Qdrant semantics.
        """
        if len(vector) != self.dim:
            raise ValueError(f"Embedding dim mismatch: got {len(vector)}, expected {self.dim}")

        dep_ids = None
        if conditions and conditions.get("deployment_id") is not None:
            d = conditions["deployment_id"]
            dep_ids = list(d) if isinstance(d, (list, tuple, set)) else [d]

        params = {
            "query_embedding": format_vector(vector),
            "p_model": self.model_name,
            "match_count": limit,
            "p_deployment_ids": dep_ids,
            "p_exclude_media_id": exclude_media_id,
        }

        def _run() -> list[dict]:
            client = create_service_client()
            return rows_of(client.rpc(_MATCH_FN, params).execute())

        rows = await asyncio.to_thread(_run)
        # `distance` is `embedding <=> query` over non-null vectors, so never NULL in
        # practice — the guard is belt-and-suspenders against a malformed row.
        return [
            SimilarPoint(
                id=str(r["media_id"]),
                score=(1.0 - float(r["distance"])) if r.get("distance") is not None else 0.0,
                payload={"deployment_id": r.get("deployment_id"), "cluster_id": r.get("cluster_id")},
            )
            for r in rows
        ]

    async def retrieve_vector(self, media_id: str) -> Optional[list[float]]:
        """Fetch a stored vector by media id (for 'find similar to media X')."""

        def _run() -> Optional[Any]:
            client = create_service_client()
            row = row_of(client.table(_TABLE).select("embedding").eq("media_id", media_id).maybe_single().execute())
            return row.get("embedding") if row else None

        return parse_vector(await asyncio.to_thread(_run))

    async def delete(self, ids: Sequence[str]) -> None:
        """Clear the stored vector for the given media ids (metadata row is kept)."""
        if not ids:
            return
        id_list = list(ids)

        def _run() -> None:
            client = create_service_client()
            # Chunk like upsert: PostgREST puts .in_() ids in the URL, so a big list
            # would blow the URL length (414). 100 per request stays well under it.
            for i in range(0, len(id_list), 100):
                client.table(_TABLE).update({"embedding": None, "embedding_model": None}).in_("media_id", id_list[i : i + 100]).execute()

        await asyncio.to_thread(_run)

    async def count(self) -> int:
        """Number of rows holding a vector for this model."""

        def _run() -> int:
            client = create_service_client()
            res = client.table(_TABLE).select("media_id", count=CountMethod.exact).eq("embedding_model", self.model_name).limit(1).execute()
            return res.count or 0

        return await asyncio.to_thread(_run)

    async def create_snapshot(self) -> Optional[str]:
        """No-op — pgvector rows are covered by Supabase PITR, so there's no snapshot to take."""
        logger.info("pgvector_snapshot_noop", model=self.model_name)
        return None

    async def health(self) -> dict[str, Any]:
        """Lightweight health probe for the admin page."""
        try:
            return {"status": "ok", "store": "pgvector", "model": self.model_name, "vectors": await self.count()}
        except Exception as exc:  # noqa: BLE001 — health must never raise
            logger.warning("pgvector_health_failed", error=str(exc))
            return {"status": "error", "store": "pgvector", "model": self.model_name, "error": str(exc)}

    async def close(self) -> None:
        """No persistent connection to close (Supabase client is per-call)."""
        return None


# Per-model service cache.
_services: dict[str, PgVectorService] = {}


def get_vector_service(model_name: Optional[str] = None) -> PgVectorService:
    """Return a ``PgVectorService`` bound to a model variant (default: server ViT-H)."""
    key = model_name or DEFAULT_SERVER_MODEL.value
    svc = _services.get(key)
    if svc is None:
        svc = PgVectorService(model_name=model_name)
        _services[key] = svc
    return svc
