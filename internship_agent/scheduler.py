"""The nightly discovery run.

Loop A only: fetch the configured boards, then screen whatever is new. The
scheduler never drafts and never submits. Drafting costs money and produces
something a person has to read, so it fires on an explicit request, not on a
timer. A test holds that line.

The job opens its own connection because APScheduler runs it on a worker
thread and sqlite3 connections are not safe to share across threads.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from internship_agent.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CRITERIA_PATH,
    DEFAULT_DB_PATH,
    build_backend,
    build_sources,
    load_config,
    load_criteria,
    resolve_resume_path,
)
from internship_agent.db.connection import connect
from internship_agent.db.events import log_event
from internship_agent.llm.base import LLMTransportError, StructuredLLM
from internship_agent.scout.run import run_scout
from internship_agent.screener.run import run_screener

log = logging.getLogger(__name__)

JOB_ID = "nightly-discovery"


def run_discovery(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    config_path: Path = DEFAULT_CONFIG_PATH,
    criteria_path: Path = DEFAULT_CRITERIA_PATH,
    client: httpx.Client | None = None,
    screener_backend: StructuredLLM | None = None,
) -> dict[str, int]:
    """Scout, then screen. Returns counts; never raises into the scheduler."""
    cfg = load_config(config_path)
    settings = load_criteria(criteria_path)
    conn = connect(db_path)
    counts: dict[str, int] = {}
    own_client = client is None
    client = client or httpx.Client(follow_redirects=False)
    try:
        scout = run_scout(
            conn, build_sources(cfg.scout), client=client, delay_s=cfg.scout.delay_seconds
        )
        counts |= {f"scout_{k}": v for k, v in scout.as_dict().items()}

        resume_text = resolve_resume_path(settings).read_text(encoding="utf-8")
        screener = run_screener(
            conn,
            screener_backend or build_backend(settings.screener),
            settings=settings,
            resume_text=resume_text,
        )
        counts |= {f"screen_{k}": v for k, v in screener.counts().items()}
    except LLMTransportError as exc:
        # The boards were still fetched; only scoring failed. Tomorrow retries.
        log.warning("nightly: screener backend unreachable: %s", exc)
        log_event(conn, "scheduler.partial", payload={"error": str(exc), **counts})
        return counts
    except Exception as exc:  # a scheduled job that raises dies silently
        log.exception("nightly: discovery failed")
        log_event(conn, "scheduler.failed", payload={"error": f"{type(exc).__name__}: {exc}"})
        return counts
    finally:
        if own_client:
            client.close()
        log_event(conn, "scheduler.discovery_finished", payload=counts)
        conn.close()
    return counts


def start_discovery_scheduler(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    config_path: Path = DEFAULT_CONFIG_PATH,
    criteria_path: Path = DEFAULT_CRITERIA_PATH,
) -> BackgroundScheduler:
    cfg = load_config(config_path).scheduler
    scheduler = BackgroundScheduler(timezone=cfg.timezone)
    if cfg.enabled:
        scheduler.add_job(
            run_discovery,
            trigger=CronTrigger(hour=cfg.hour, minute=cfg.minute, timezone=cfg.timezone),
            id=JOB_ID,
            name="nightly discovery (scout + screener)",
            kwargs={
                "db_path": db_path,
                "config_path": config_path,
                "criteria_path": criteria_path,
            },
            max_instances=1,
            coalesce=True,  # a laptop that was asleep runs one catch-up, not five
            misfire_grace_time=3600,
        )
    scheduler.start()
    return scheduler
