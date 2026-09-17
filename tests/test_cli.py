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
