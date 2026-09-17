"""API response models.

Separate from the agent models on purpose: the agents' schemas are prompt
contracts and changing one to suit the dashboard would change what the model
is asked for. These are presentation shapes, free to evolve with the UI.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from internship_agent.agents.critic import Critique
from internship_agent.writer.models import Bullet


class Health(BaseModel):
    status: str
    database: str
    schema_version: int | None
    postings: int
    applications: int


class PostingOut(BaseModel):
    posting_id: int
    company: str
    title: str
    location: str | None
    url: str
    first_seen_at: str
    last_seen_at: str
    status: str


class QueueItem(BaseModel):
    posting_id: int
    company: str
    title: str
    location: str | None
    url: str
    fit_score: float
    reason: str
    model: str
    screened_at: str


class ApplicationSummary(BaseModel):
    application_id: int
    posting_id: int
    company: str
    title: str
    location: str | None
    url: str
    status: str
    rounds: int
    first_overall: float | None
    final_overall: float | None
    final_blockers: int | None
    created_at: str
    updated_at: str


class RoundDetail(BaseModel):
    round_index: int
    draft_id: int
    bullets: list[Bullet]
    cover_letter: str
    writer_model: str | None
    voice_hits: list[str] = Field(default_factory=list)
    critique_leaks: list[str] = Field(default_factory=list)
    critique: Critique | None = None


class ApplicationDetail(ApplicationSummary):
    # `rounds` (inherited) is the count; `drafts` is one entry per round, each
    # with the critique of that draft. Naming both "rounds" would have made one
    # field a number in the list view and a list in the detail view.
    drafts: list[RoundDetail] = Field(default_factory=list)


class LoopRunRequest(BaseModel):
    posting_id: int


class RoundScore(BaseModel):
    round_index: int
    overall: float
    unsupported_claim_count: int
    verdict: str


class LoopRunResponse(BaseModel):
    application_id: int | None
    status: str
    rounds_completed: int
    stopped_because: str
    stopped_detail: str | None = None
    scores_by_round: list[RoundScore] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    note: str = ""


class DecisionResponse(BaseModel):
    application_id: int
    status: str
    message: str


class ScoreByRound(BaseModel):
    round_index: int
    mean_overall: float
    applications: int


class ScoreByDimension(BaseModel):
    round_index: int
    dimension: str
    mean_score: float
    applications: int


class SchedulerStatus(BaseModel):
    running: bool
    jobs: list[dict[str, str | None]]
