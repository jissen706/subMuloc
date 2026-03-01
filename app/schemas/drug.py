"""
Request/response schemas for drug ingestion endpoints.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=512, description="Drug name or code to ingest")


class IngestResponse(BaseModel):
    drug_id: str
    canonical_name: str
    status: str  # "queued" | "completed" | "error"
    task_id: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    message: str | None = None
