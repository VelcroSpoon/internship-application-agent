"""CLI entry point. The scout subcommand needs the network, so it is
exercised with an injected client; the db subcommands run for real."""

import json
from pathlib import Path

import httpx

from internship_agent.__main__ import main
from internship_agent.db.connection import connect

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_scaleai.json"


def _write_config(tmp_path: Path) -> Path:
    p = tmp_path / "sources.toml"
    p.write_text(
        '[scout]\nuser_agent = "X/1 (+mailto:a@b)"\ndelay_seconds = 1.0\n'
        '[[scout.greenhouse]]\nboard = "scaleai"\ncompany = "Scale AI"\n',
        encoding="utf-8",
    )
    return p


def _fixture_client() -> httpx.Client:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /embed/\n")
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_db_migrate_creates_database_file(tmp_path: Path, capsys):
    db = tmp_path / "agent.db"

    code = main(["db", "migrate", "--db", str(db)])

    assert code == 0
    assert db.exists()
    assert "applied [1]" in capsys.readouterr().out


def test_scout_run_twice_populates_once(tmp_path: Path, capsys):
    db = tmp_path / "agent.db"
    cfg = _write_config(tmp_path)

    first = main(["scout", "run", "--db", str(db), "--config", str(cfg)], client=_fixture_client())
    second = main(["scout", "run", "--db", str(db), "--config", str(cfg)], client=_fixture_client())

    assert first == 0 and second == 0
    out = capsys.readouterr().out
    assert "new=4" in out and "seen=4" in out
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 4
    conn.close()


def test_postings_list_prints_rows(tmp_path: Path, capsys):
    db = tmp_path / "agent.db"
    cfg = _write_config(tmp_path)
    main(["scout", "run", "--db", str(db), "--config", str(cfg)], client=_fixture_client())
    capsys.readouterr()

    code = main(["postings", "list", "--db", str(db)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Software Engineering Intern (Summer 2027)" in out
    assert out.count("Scale AI") == 4


# --- screener + queue -------------------------------------------------------


def _write_criteria(tmp_path: Path) -> Path:
    resume = tmp_path / "resume.md"
    resume.write_text("# Jane\n- Python, PyTorch\n", encoding="utf-8")
    p = tmp_path / "criteria.toml"
    p.write_text(
        f'[candidate]\nmaster_resume_path = "{resume.as_posix()}"\n'
        '[criteria]\ntarget_cycle = "Summer 2027"\nqueue_threshold = 70\n'
        '[prefilter]\ntitle_patterns = ["intern"]\n'
        '[screener]\nmodel = "fake"\n',
        encoding="utf-8",
    )
    return p


def _seeded(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "agent.db"
    cfg = _write_config(tmp_path)
    main(["scout", "run", "--db", str(db), "--config", str(cfg)], client=_fixture_client())
    return db, _write_criteria(tmp_path)


def test_screener_dry_run_prints_scores_and_writes_nothing(tmp_path: Path, capsys):
    from internship_agent.screener.models import Screening
    from tests.fakes import FakeLLM

    db, criteria = _seeded(tmp_path)
    capsys.readouterr()
    llm = FakeLLM(
        [
            Screening(
                fit_score=s,
                reason="r",
                is_internship=True,
                matched_requirements=[],
                missing_requirements=[],
            )
            for s in (81, 33, 90)
        ]
    )

    code = main(
        ["screener", "run", "--db", str(db), "--criteria", str(criteria), "--dry-run"],
        backend=llm,
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "81" in out and "90" in out
    assert "title matched no prefilter" not in out  # 200 such rows would bury the real ones
    assert "scored=3" in out and "prefiltered=1" in out
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0] == 0
    conn.close()


def test_screener_run_then_queue_list(tmp_path: Path, capsys):
    from internship_agent.screener.models import Screening
    from tests.fakes import FakeLLM

    db, criteria = _seeded(tmp_path)
    llm = FakeLLM(
        [
            Screening(
                fit_score=s,
                reason=f"reason-{s}",
                is_internship=True,
                matched_requirements=[],
                missing_requirements=[],
            )
            for s in (81, 33, 90)
        ]
    )
    main(["screener", "run", "--db", str(db), "--criteria", str(criteria)], backend=llm)
    capsys.readouterr()

    code = main(["queue", "list", "--db", str(db), "--criteria", str(criteria)])

    out = capsys.readouterr().out
    assert code == 0
    assert "reason-90" in out and "reason-81" in out and "reason-33" not in out
    assert out.index("reason-90") < out.index("reason-81")
    assert "2 queued" in out


# --- writer + drafts --------------------------------------------------------


def _draft_backend():
    from internship_agent.writer.models import Bullet, Draft
    from tests.fakes import FakeLLM

    letter = "I built a MinHash deduper in pandas for 40k tickets. " * 8
    return FakeLLM(
        [
            Draft(
                bullets=[
                    Bullet(text=f"Bullet {i} on PyTorch.", resume_anchor=f"line {i}")
                    for i in range(3)
                ],
                cover_letter=letter,
            )
        ]
    )


def test_writer_draft_dry_run_prints_draft_and_writes_nothing(tmp_path: Path, capsys):
    db, criteria = _seeded(tmp_path)
    capsys.readouterr()

    code = main(
        [
            "writer",
            "draft",
            "--posting",
            "1",
            "--db",
            str(db),
            "--criteria",
            str(criteria),
            "--dry-run",
        ],
        backend=_draft_backend(),
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Bullet 0 on PyTorch." in out and "MinHash deduper" in out
    assert "dry-run" in out
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    conn.close()


def test_writer_draft_persists_then_drafts_show_renders_it(tmp_path: Path, capsys):
    db, criteria = _seeded(tmp_path)
    capsys.readouterr()

    code = main(
        ["writer", "draft", "--posting", "1", "--db", str(db), "--criteria", str(criteria)],
        backend=_draft_backend(),
    )
    out = capsys.readouterr().out
    assert code == 0 and "application 1" in out and "draft 1" in out

    code = main(["drafts", "show", "--application", "1", "--db", str(db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "round 0" in out and "Bullet 2 on PyTorch." in out and "line 2" in out


def test_writer_draft_refuses_second_draft(tmp_path: Path, capsys):
    db, criteria = _seeded(tmp_path)
    args = ["writer", "draft", "--posting", "1", "--db", str(db), "--criteria", str(criteria)]
    main(args, backend=_draft_backend())
    capsys.readouterr()

    code = main(args, backend=_draft_backend())

    assert code == 1
    assert "already has a draft" in capsys.readouterr().err
