"""Command-line entry point: ``python -m internship_agent <group> <command>``.

Thin glue only. Anything with logic lives in a module with its own tests;
this file parses arguments, opens the database, and prints results.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from internship_agent.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CRITERIA_PATH,
    DEFAULT_DB_PATH,
    build_screener_backend,
    build_sources,
    load_config,
    load_criteria,
    resolve_resume_path,
)
from internship_agent.db.connection import connect
from internship_agent.db.migrate import migrate
from internship_agent.llm.base import LLMTransportError, StructuredLLM
from internship_agent.scout.run import run_scout
from internship_agent.screener.queue import queue_postings
from internship_agent.screener.run import run_screener


def _open(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    migrate(conn)
    return conn


# --- db -----------------------------------------------------------------------


def cmd_db_migrate(args: argparse.Namespace, **_: object) -> int:
    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(args.db)
    applied = migrate(conn)
    conn.close()
    print(f"{args.db}: applied {applied}" if applied else f"{args.db}: up to date")
    return 0


# --- scout --------------------------------------------------------------------


def cmd_scout_run(args: argparse.Namespace, client: httpx.Client | None = None, **_: object) -> int:
    cfg = load_config(args.config)
    sources = build_sources(cfg.scout)
    conn = _open(args.db)
    own_client = client is None
    client = client or httpx.Client(follow_redirects=False)
    try:
        summary = run_scout(conn, sources, client=client, delay_s=cfg.scout.delay_seconds)
    finally:
        if own_client:
            client.close()
        conn.close()
    print(
        f"scout: sources={summary.sources} new={summary.new} seen={summary.seen} "
        f"updated={summary.updated} collisions={summary.collisions} errors={summary.errors}"
    )
    return 1 if summary.errors and summary.errors == summary.sources else 0


def cmd_postings_list(args: argparse.Namespace, **_: object) -> int:
    conn = _open(args.db)
    rows = conn.execute(
        "SELECT id, company, title, location, first_seen_at, last_seen_at, status "
        "FROM postings ORDER BY first_seen_at DESC, id DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    conn.close()
    for r in rows:
        seen = f"{r['first_seen_at'][:10]}..{r['last_seen_at'][:10]}"
        print(
            f"{r['id']:>5}  {r['company'][:20]:<20}  {r['title'][:48]:<48}  "
            f"{(r['location'] or '')[:22]:<22}  seen {seen}"
        )
    print(f"{len(rows)} posting(s)")
    return 0


# --- screener -----------------------------------------------------------------


def cmd_screener_run(
    args: argparse.Namespace, backend: StructuredLLM | None = None, **_: object
) -> int:
    settings = load_criteria(args.criteria)
    resume_text = resolve_resume_path(settings).read_text(encoding="utf-8")
    backend = backend or build_screener_backend(settings.screener)
    conn = _open(args.db)
    try:
        summary = run_screener(
            conn,
            backend,
            settings=settings,
            resume_text=resume_text,
            dry_run=args.dry_run,
            limit=args.limit,
            rescreen=args.rescreen,
        )
    except LLMTransportError as exc:
        print(f"screener: aborted, backend unreachable: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    for r in sorted(summary.results, key=lambda r: -r.fit_score):
        print(
            f"{r.fit_score:>4}  {r.company[:18]:<18}  {r.title[:44]:<44}  "
            f"{r.model[:14]:<14}  {r.reason[:70]}"
        )
    mode = "dry-run, nothing written" if args.dry_run else "written"
    print(
        f"screener ({mode}): considered={summary.considered} scored={summary.scored} "
        f"prefiltered={summary.prefiltered} failed={summary.failed}"
    )
    return 0


def cmd_queue_list(args: argparse.Namespace, **_: object) -> int:
    settings = load_criteria(args.criteria)
    conn = _open(args.db)
    rows = queue_postings(conn, threshold=settings.criteria.queue_threshold)
    conn.close()
    for r in rows:
        print(
            f"{r['posting_id']:>5}  {r['fit_score']:>4.0f}  {r['company'][:18]:<18}  "
            f"{r['title'][:44]:<44}  {(r['location'] or '')[:20]:<20}  {r['reason'][:60]}"
        )
    print(f"{len(rows)} queued (threshold {settings.criteria.queue_threshold})")
    return 0


# --- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # Shared options live on a parent parser attached to every leaf command, so
    # `scout run --db x` works. argparse only accepts top-level options *before*
    # the subcommand, which nobody types.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite file")
    common.add_argument("-v", "--verbose", action="store_true")

    criteria_opt = argparse.ArgumentParser(add_help=False)
    criteria_opt.add_argument("--criteria", type=Path, default=DEFAULT_CRITERIA_PATH)

    parser = argparse.ArgumentParser(prog="internship_agent")
    groups = parser.add_subparsers(dest="group", required=True)

    db = groups.add_parser("db", help="database maintenance").add_subparsers(
        dest="command", required=True
    )
    db.add_parser("migrate", parents=[common], help="apply pending migrations").set_defaults(
        func=cmd_db_migrate
    )

    scout = groups.add_parser("scout", help="discovery loop").add_subparsers(
        dest="command", required=True
    )
    run = scout.add_parser("run", parents=[common], help="fetch all configured sources once")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    run.set_defaults(func=cmd_scout_run)

    postings = groups.add_parser("postings", help="inspect postings").add_subparsers(
        dest="command", required=True
    )
    ls = postings.add_parser("list", parents=[common])
    ls.add_argument("--limit", type=int, default=50)
    ls.set_defaults(func=cmd_postings_list)

    screener = groups.add_parser("screener", help="score postings against criteria").add_subparsers(
        dest="command", required=True
    )
    srun = screener.add_parser("run", parents=[common, criteria_opt])
    srun.add_argument("--dry-run", action="store_true", help="score and print, write nothing")
    srun.add_argument("--limit", type=int, default=None, help="max postings to consider")
    srun.add_argument("--rescreen", action="store_true", help="include already-screened postings")
    srun.set_defaults(func=cmd_screener_run)

    queue = groups.add_parser("queue", help="postings above threshold").add_subparsers(
        dest="command", required=True
    )
    queue.add_parser("list", parents=[common, criteria_opt]).set_defaults(func=cmd_queue_list)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
    backend: StructuredLLM | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args, client=client, backend=backend)


if __name__ == "__main__":
    sys.exit(main())
