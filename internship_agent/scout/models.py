"""Normalised posting record: the one shape every source must produce."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from internship_agent.scout.dedupe import content_hash, dedupe_hash


class PostingRecord(BaseModel):
    source: str = Field(description="'<adapter>:<board>' e.g. 'greenhouse:scaleai'")
    external_id: str | None = Field(default=None, description="The board's own job id")
    company: str
    title: str
    location: str | None = None
    url: str
    description: str = Field(default="", description="Plain text, HTML stripped")
    posted_at: str | None = Field(default=None, description="ISO 8601 if the board reports it")
    raw: dict[str, Any] = Field(default_factory=dict, description="Original payload")

    @field_validator("company", "title", "location", "url")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    @property
    def dedupe_hash(self) -> str:
        return dedupe_hash(self.company, self.title, self.location)

    @property
    def content_hash(self) -> str:
        return content_hash(self.description)
