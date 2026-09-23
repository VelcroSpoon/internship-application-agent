"""FastAPI app.

Two shapes of endpoint. Reads are ordinary queries. ``POST /loop/run`` is
deliberately blocking: it holds the connection for the two or three minutes
a full Writer/Critic loop takes. A job table plus polling would be more
moving parts than a single-user tool has problems, and the loop already
persists every round as it goes, so a dropped connection loses the response,
not the work.

One SQLite connection per request, opened and closed by a dependency.
FastAPI runs sync endpoints in a threadpool and sqlite3 connections are not
safe to share across threads, so per-request connections sidestep the issue
instead of managing it.

Nothing here submits an application. The scheduler runs discovery only; the
drafting loop fires on an explicit request, and it stops at the human gate.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from internship_agent.agents.critic import Critique
from internship_agent.api.schemas import (
    ApplicationDetail,
    ApplicationRef,
    ApplicationSummary,
    DecisionRequest,
    DecisionResponse,
    EditRequest,
    Health,
    LoopRunRequest,
    LoopRunResponse,
    PostingDetail,
    PostingOut,
    QueueItem,
    RoundDetail,
    RoundScore,
    SchedulerStatus,
    ScoreByDimension,
    ScoreByRound,
    ScreeningOut,
)
from internship_agent.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CRITERIA_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_VOICE_PATH,
    build_backend,
    load_criteria,
    load_voice,
)
from internship_agent.db.connection import connect
from internship_agent.db.migrate import applied_versions, migrate
from internship_agent.llm.base import StructuredLLM
from internship_agent.orchestrator import LoopRefused, run_loop
from internship_agent.review import (
    InvalidTransition,
    approve,
    list_applications,
    mark_submitted,
    reject,
    save_human_draft,
)
from internship_agent.screener.queue import queue_postings
from internship_agent.writer.models import Bullet


def create_app(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    config_path: Path = DEFAULT_CONFIG_PATH,
    criteria_path: Path = DEFAULT_CRITERIA_PATH,
    voice_path: Path = DEFAULT_VOICE_PATH,
    writer_backend: StructuredLLM | None = None,
    critic_backend: StructuredLLM | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(db_path)
        migrate(conn)
        conn.close()
        app.state.scheduler = None
        if start_scheduler:
            from internship_agent.scheduler import start_discovery_scheduler

            app.state.scheduler = start_discovery_scheduler(
                db_path=db_path, config_path=config_path, criteria_path=criteria_path
            )
        yield
        if app.state.scheduler is not None:
            app.state.scheduler.shutdown(wait=False)

    app = FastAPI(
        title="Internship Application Agent",
        version="0.1.0",
        summary="Finds postings and drafts materials. Never submits anything.",
        lifespan=lifespan,
    )
    app.state.db_path = db_path
    app.state.criteria_path = criteria_path
    app.state.voice_path = voice_path
    app.state.writer_backend = writer_backend
    app.state.critic_backend = critic_backend

    def get_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    Db = Depends(get_db)

    # --- reads ---------------------------------------------------------------

    @app.get("/health", response_model=Health)
    def health(conn: sqlite3.Connection = Db) -> Health:
        versions = applied_versions(conn)
        return Health(
            status="ok",
            database=str(db_path),
            schema_version=versions[-1] if versions else None,
            postings=conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0],
            applications=conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
        )

    @app.get("/postings", response_model=list[PostingOut])
    def postings(
        limit: int = Query(50, ge=1, le=500), conn: sqlite3.Connection = Db
    ) -> list[PostingOut]:
        rows = conn.execute(
            "SELECT id AS posting_id, company, title, location, url, first_seen_at, "
            "last_seen_at, status FROM postings ORDER BY first_seen_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [PostingOut(**dict(r)) for r in rows]

    @app.get("/postings/{posting_id}", response_model=PostingDetail)
    def posting_detail(posting_id: int, conn: sqlite3.Connection = Db) -> PostingDetail:
        row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no posting with id {posting_id}")
        return PostingDetail(
            posting_id=row["id"],
            company=row["company"],
            title=row["title"],
            location=row["location"],
            url=row["url"],
            source=row["source"],
            external_id=row["external_id"],
            description=row["description"],
            posted_at=row["posted_at"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            status=row["status"],
            screening=_latest_screening(conn, posting_id),
            application=_application_ref(conn, posting_id),
        )

    @app.get("/queue", response_model=list[QueueItem])
    def queue(conn: sqlite3.Connection = Db) -> list[QueueItem]:
        settings = load_criteria(criteria_path)
        rows = queue_postings(conn, threshold=settings.criteria.queue_threshold)
        return [
            QueueItem(
                posting_id=r["posting_id"],
                company=r["company"],
                title=r["title"],
                location=r["location"],
                url=r["url"],
                source=r["source"],
                posted_at=r["posted_at"],
                first_seen_at=r["first_seen_at"],
                fit_score=r["fit_score"],
                reason=r["reason"],
                model=r["model"],
                screened_at=r["screened_at"],
            )
            for r in rows
        ]

    @app.get("/applications", response_model=list[ApplicationSummary])
    def applications(
        status: str | None = None, conn: sqlite3.Connection = Db
    ) -> list[ApplicationSummary]:
        return [ApplicationSummary(**dict(r)) for r in list_applications(conn, status=status)]

    @app.get("/applications/{application_id}", response_model=ApplicationDetail)
    def application_detail(application_id: int, conn: sqlite3.Connection = Db) -> ApplicationDetail:
        rows = list_applications(conn)
        summary = next((r for r in rows if r["application_id"] == application_id), None)
        if summary is None:
            raise HTTPException(404, f"no application with id {application_id}")
        return ApplicationDetail(**dict(summary), drafts=_drafts_for(conn, application_id))

    # --- the loop (blocking) --------------------------------------------------

    @app.post("/loop/run", response_model=LoopRunResponse)
    def loop_run(body: LoopRunRequest, request: Request) -> LoopRunResponse:
        settings = load_criteria(criteria_path)
        voice = load_voice(voice_path)
        resume_text = _resume_text(settings)
        conn = connect(db_path)
        try:
            result = run_loop(
                conn,
                writer_backend=request.app.state.writer_backend or build_backend(settings.writer),
                critic_backend=request.app.state.critic_backend or build_backend(settings.critic),
                posting_id=body.posting_id,
                resume_text=resume_text,
                voice=voice,
                settings=settings,
            )
            status = conn.execute(
                "SELECT status FROM applications WHERE id = ?", (result.application_id,)
            ).fetchone()[0]
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except LoopRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            conn.close()

        return LoopRunResponse(
            application_id=result.application_id,
            status=status,
            rounds_completed=result.rounds_completed,
            stopped_because=result.stopped_because,
            stopped_detail=result.stopped_detail,
            scores_by_round=[
                RoundScore(
                    round_index=c.round_index,
                    overall=c.overall,
                    unsupported_claim_count=c.unsupported_claim_count,
                    verdict=c.verdict.value,
                )
                for c in result.history
            ],
        )

    # --- the human gate -------------------------------------------------------

    def _decide(action, application_id: int, body: DecisionRequest, conn, message: str):
        try:
            action(conn, application_id, note=body.note)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        status = conn.execute(
            "SELECT status FROM applications WHERE id = ?", (application_id,)
        ).fetchone()[0]
        return DecisionResponse(application_id=application_id, status=status, message=message)

    @app.post("/applications/{application_id}/approve", response_model=DecisionResponse)
    def approve_application(
        application_id: int, body: DecisionRequest, conn: sqlite3.Connection = Db
    ) -> DecisionResponse:
        return _decide(
            approve,
            application_id,
            body,
            conn,
            "Approved. Nothing is submitted for you; copy the draft and apply yourself.",
        )

    @app.post("/applications/{application_id}/reject", response_model=DecisionResponse)
    def reject_application(
        application_id: int, body: DecisionRequest, conn: sqlite3.Connection = Db
    ) -> DecisionResponse:
        return _decide(reject, application_id, body, conn, "Rejected.")

    @app.post("/applications/{application_id}/submitted", response_model=DecisionResponse)
    def mark_application_submitted(
        application_id: int, body: DecisionRequest, conn: sqlite3.Connection = Db
    ) -> DecisionResponse:
        return _decide(
            mark_submitted, application_id, body, conn, "Recorded that you submitted it."
        )

    @app.post("/applications/{application_id}/drafts", response_model=ApplicationDetail)
    def edit_application(
        application_id: int, body: EditRequest, conn: sqlite3.Connection = Db
    ) -> ApplicationDetail:
        try:
            save_human_draft(
                conn,
                application_id,
                bullets=[b.model_dump() for b in body.bullets],
                cover_letter=body.cover_letter,
                note=body.note,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        return application_detail(application_id, conn)

    # --- stats ----------------------------------------------------------------

    @app.get("/stats/scores-by-round", response_model=list[ScoreByRound])
    def scores_by_round(conn: sqlite3.Connection = Db) -> list[ScoreByRound]:
        rows = conn.execute(
            # Only the Writer's rounds. A human edit has no critique, so it could
            # not appear anyway, but the join says so rather than relying on that.
            "SELECT c.round_index, ROUND(AVG(c.overall), 3) AS mean_overall, "
            "COUNT(*) AS applications FROM critiques c "
            "JOIN drafts d ON d.id = c.draft_id AND d.authored_by = 'writer' "
            "GROUP BY c.round_index ORDER BY c.round_index"
        ).fetchall()
        return [ScoreByRound(**dict(r)) for r in rows]

    @app.get("/stats/scores-by-dimension", response_model=list[ScoreByDimension])
    def scores_by_dimension(conn: sqlite3.Connection = Db) -> list[ScoreByDimension]:
        rows = conn.execute(
            "SELECT c.round_index, s.dimension, ROUND(AVG(s.score), 3) AS mean_score, "
            "COUNT(*) AS applications FROM critique_scores s "
            "JOIN critiques c ON c.id = s.critique_id "
            "JOIN drafts d ON d.id = c.draft_id AND d.authored_by = 'writer' "
            "GROUP BY c.round_index, s.dimension ORDER BY c.round_index, s.dimension"
        ).fetchall()
        return [ScoreByDimension(**dict(r)) for r in rows]

    @app.get("/scheduler", response_model=SchedulerStatus)
    def scheduler_status(request: Request) -> SchedulerStatus:
        sched = request.app.state.scheduler
        if sched is None:
            return SchedulerStatus(running=False, jobs=[])
        return SchedulerStatus(
            running=sched.running,
            jobs=[
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": str(job.next_run_time) if job.next_run_time else None,
                }
                for job in sched.get_jobs()
            ],
        )

    return app


# --- helpers ---------------------------------------------------------------------


def _resume_text(settings) -> str:
    from internship_agent.config import resolve_resume_path

    return resolve_resume_path(settings).read_text(encoding="utf-8")


def _latest_screening(conn: sqlite3.Connection, posting_id: int) -> ScreeningOut | None:
    row = conn.execute(
        "SELECT * FROM screenings WHERE posting_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (posting_id,),
    ).fetchone()
    if row is None:
        return None
    raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
    screening = raw.get("screening", {})
    return ScreeningOut(
        model=row["model"],
        fit_score=row["fit_score"],
        reason=row["reason"],
        is_internship=screening.get("is_internship"),
        matched_requirements=screening.get("matched_requirements", []),
        missing_requirements=screening.get("missing_requirements", []),
        disqualifiers=raw.get("disqualifiers", []),
        created_at=row["created_at"],
    )


def _application_ref(conn: sqlite3.Connection, posting_id: int) -> ApplicationRef | None:
    row = conn.execute(
        # Only an application with a draft counts: an empty one is a failed
        # round 0, and the posting page should offer drafting again.
        "SELECT a.id, a.status FROM applications a WHERE a.posting_id = ? "
        "AND EXISTS (SELECT 1 FROM drafts d WHERE d.application_id = a.id) "
        "ORDER BY a.id LIMIT 1",
        (posting_id,),
    ).fetchone()
    return None if row is None else ApplicationRef(application_id=row["id"], status=row["status"])


def _drafts_for(conn: sqlite3.Connection, application_id: int) -> list[RoundDetail]:
    rows = conn.execute(
        "SELECT d.*, c.critique_json FROM drafts d "
        "LEFT JOIN critiques c ON c.draft_id = d.id "
        "WHERE d.application_id = ? ORDER BY d.round_index",
        (application_id,),
    ).fetchall()
    out: list[RoundDetail] = []
    for r in rows:
        extra = json.loads(r["usage_json"]) if r["usage_json"] else {}
        out.append(
            RoundDetail(
                round_index=r["round_index"],
                draft_id=r["id"],
                authored_by=r["authored_by"],
                bullets=[Bullet.model_validate(b) for b in json.loads(r["bullets_json"])],
                cover_letter=r["cover_letter"],
                writer_model=r["writer_model"],
                voice_hits=extra.get("voice_hits", []),
                critique_leaks=extra.get("critique_leaks", []),
                critique=(
                    Critique.model_validate_json(r["critique_json"]) if r["critique_json"] else None
                ),
            )
        )
    return out
