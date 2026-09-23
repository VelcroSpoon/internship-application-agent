# Internship application agent

Finds internship postings, screens them against my criteria, and drafts tailored
resume bullets and cover letters through a Writer/Critic revision loop. It then
stops and waits for me.

**It never submits anything.** No form filling, no email, no browser automation
against an employer's site. It produces text; I read it, edit it, and apply
myself. That is the product, not a missing feature.

## How it fits together

Two independent loops share one SQLite database.

```
Loop A: discovery (nightly, on a timer)

  boards ──► Scout ──► postings ──► Screener ──► screenings ──► queue
            dedupe                  one small-model
            robots.txt              call per posting

Loop B: drafting (only when I ask, for one posting)

  posting ──► Writer ──► draft ──► Critic ──► critique ──┐
                ▲                                        │
                └──── revise ◄── should_continue()? ◄────┘
                                        │ no
                                        ▼
                                 awaiting_review ──► me: approve / edit / reject
```

The scheduler runs Loop A only. A test asserts the nightly job creates no
applications, drafts or critiques: drafting costs money and produces text that
is worthless until someone reads it.

| piece | where |
|---|---|
| Critic rubric, schema, weights, stop conditions (the spec, untouched) | `internship_agent/agents/critic.py` |
| Scout: Greenhouse source, dedupe, upsert | `internship_agent/scout/` |
| Screener: prompt, title pre-filter, disqualifiers, queue | `internship_agent/screener/` |
| Writer, VOICE tripwire | `internship_agent/writer/` |
| Critic runner, prose-leak guard | `internship_agent/critic/` |
| The revision loop | `internship_agent/orchestrator.py` |
| Human gate: approve, reject, edit | `internship_agent/review.py` |
| FastAPI app and nightly scheduler | `internship_agent/api/`, `internship_agent/scheduler.py` |
| Eval harness, record and replay | `internship_agent/evals/` |
| Dashboard | `dashboard/` |
| Everything I edit by hand | `config/` |

## Running it

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 20+, and for the
local Screener, [Ollama](https://ollama.com) with `qwen2.5:3b` pulled. The Writer
and Critic need an Anthropic credential: `ANTHROPIC_API_KEY` in the environment,
or a profile from `ant auth login`.

```bash
uv sync
uv run python -m internship_agent scout run          # fetch the configured boards
uv run python -m internship_agent screener run       # score what is new
uv run python -m internship_agent queue list         # what cleared the threshold
uv run python -m internship_agent loop run --posting 154
uv run python -m internship_agent applications show --id 1
```

The API, with the nightly scheduler running inside it:

```bash
uv run python -m internship_agent serve
```

That scheduler only fires while `serve` is running. To run the nightly job
without keeping a terminal open, have the operating system call the same job
as one command:

```bash
uv run python -m internship_agent discover run   # scout every board, then screen what is new
```

It never drafts. On Windows, register it with Task Scheduler; a laptop that is
asleep at 3am runs it on wake:

```powershell
$repo = "C:\path\to\internship-application-agent"
$action = New-ScheduledTaskAction -Execute "uv" -Argument "--directory `"$repo`" run python -m internship_agent discover run"
Register-ScheduledTask -TaskName "InternshipAgentDiscovery" -Action $action `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 3am) `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```

The local Screener needs Ollama running at that hour; its installer adds it to
the Startup folder, so it runs whenever you are logged in.

The dashboard, in a second terminal, then open `http://localhost:3000`:

```bash
cd dashboard && npm install && npm run dev
```

`config/master_resume.md` ships as a **fictional** resume. Replace it with your
own before drafting anything real. Every claim the Writer makes must trace to a
line in that file.

## Configuration

Everything I tune lives in TOML under `config/`, and every model rejects unknown
keys so a typo fails loudly instead of silently disabling something.

- `sources.toml`: boards to scout, the User-Agent, the delay between boards
  (floored at one second; politeness is not configurable away), and the nightly
  schedule.
- `criteria.toml`: target cycle, roles and locations, the queue threshold,
  title pre-filter patterns, hard disqualifiers, and which backend and model
  each agent uses.
- `voice.toml`: the VOICE anti-pattern list: banned phrases, banned regex
  patterns, and style notes. Edited without touching code.

## The revision loop

The orchestrator owns the stop decision and makes it by calling
`should_continue()` from the spec file. It re-implements nothing.
`describe_stop()` only names which gate fired, and a test holds it to agreeing
with `should_continue()` on every history.

Three things the Critic reports are recomputed from its own findings before the
gate reads them, because a model doing weighted arithmetic in its head is not
reliable:

| field | recomputed as |
|---|---|
| `overall` | `compute_overall(scores)`, the spec's weights |
| `unsupported_claim_count` | count of blocker-severity grounding findings, the field's own definition |
| `round_index` | the round the orchestrator is actually on |

No weight or threshold changes. What the model claimed is stored beside the
recomputed value, so the gap is measurable. `verdict` is left exactly as the
model set it: the rubric calls it advisory and the gate never reads it.

**The Critic never writes prose.** Its schema has no field that could hold
replacement text, and the Writer is told to act on findings in its own words.
Because a prompt is not a guarantee, `find_critique_leaks()` detects any
eight-word run shared between a finding's `fix_direction` and the draft it
produced, and records it on the draft. The dashboard shows it in red.

**Failures degrade instead of crashing.** A Critic that returns malformed JSON
twice, a failed revision, or an unreachable backend each stop the loop, log an
event, and leave an application I can still open. Only a first draft that never
appears leaves nothing, and that application stays `drafting`.

## Data

Two migrations, applied forward-only, one transaction each. The keys are shaped
so the question the project exists to answer is one query:

```sql
SELECT round_index, AVG(overall) FROM critiques GROUP BY round_index;
```

Every revision is persisted with its full `Critique` and its round index before
the next round starts. Per-dimension scores are unpacked into
`critique_scores` so the per-dimension version is also one `GROUP BY`. My own
edits are ordinary draft rows marked `authored_by = 'human'`, and every stats
query joins on `authored_by = 'writer'`, so my editing can never read as the
model improving.

## Evals

Does the Critic loop actually improve drafts, or does it plateau after round 2?

The eval runs two arms over the four real postings in
`tests/fixtures/greenhouse_scaleai.json`:

- **loop**: the real orchestrator, up to three rounds.
- **control**: one Writer call, then one Critic call. No revision.

Both are scored by the same Critic. Model calls are **recorded once and replayed
forever**: recording costs roughly two dollars and needs a credential; every
run after that is free, offline, and produces the identical report.

```bash
uv run python -m internship_agent evals record   # once; refuses to overwrite without --force
uv run python -m internship_agent evals run      # replay; writes evals/results/report.md
```

Three numbers carry the result, each chosen against a specific way of fooling
myself:

1. **Paired round deltas.** Round r's delta uses only postings that reached
   round r, each against its own round r−1. The loop only continues on drafts
   that started badly, so comparing raw round means rewards survivorship.
2. **Lift over control.** Mean final-round score minus mean single-pass score:
   the whole loop's contribution.
3. **The noise floor.** Mean absolute gap between the control and the loop's
   own round 0. Both are single-pass drafts of the same prompt, so their gap is
   sampling, not method. A lift no larger than that is not evidence of anything.

Recordings are keyed by a hash of the arm, model, schema and both prompts.
Change a prompt, the resume, or `voice.toml`, and replay fails with an
instruction to re-record, rather than silently answering a question with a
response to a different one. The arm is in the key on purpose: the control's
draft uses exactly the loop's round-0 prompt, and without it the two would share
one recording and the control would equal round 0 by construction.

Edit magnitude per revision, the share of words changed, is computed here in
Python. The dashboard's diff is a separate view concern and shares no code.

**Status:** the harness is built and tested end to end against scripted models,
including an exact record-then-replay reproduction over the real posting text.
No live recording exists yet, so there is no result to report. The four fixture
postings include two non-internship roles, which is deliberate pressure on
grounding but also means the sample is small and mixed. Treat a first result as
a direction, not a finding.

## Design decisions

The ones worth being able to defend:

- **Plain `sqlite3`, no ORM.** One user, one process, and the interesting output
  is a handful of analytics queries. Hand-written SQL migrations keep the schema
  readable, and Pydantic already owns the object layer at the model boundary.
- **Deterministic where a rule is deterministic.** The title pre-filter and hard
  disqualifiers are regexes applied in code. A 3B model handed a checklist
  copied it back verbatim as findings instead of reading the posting.
- **Local Screener, hosted Writer and Critic.** The Screener runs nightly over
  hundreds of postings, so cost dominates; drafting runs a few times a week, so
  quality does. Any agent switches backend with one config line.
- **A blocking loop endpoint.** One user. The loop persists each round as it
  goes, so a dropped connection loses the response, not the work.
- **One SQLite connection per request.** FastAPI runs sync endpoints in a
  threadpool and `sqlite3` connections are not thread-safe; per-request
  connections sidestep that rather than managing it.
- **Client-side word diff, inline.** A diff is a view concern, and both
  revisions already reach the browser. A cover letter is prose; side-by-side
  columns mean aligning two blocks by eye.

## Known quirks

- `should_continue()`'s plateau check compares a float subtraction against
  `MIN_DELTA`. Of the 76 reachable score pairs whose nominal gain is exactly
  0.15, 50 stop the loop and 26 continue, purely on IEEE 754 representation. A
  draft that gained exactly the minimum is a plateau under either answer, so it
  is pinned in a test rather than changed.
- The local Screener on `qwen2.5:3b` is a coarse filter and echoes lists from
  its prompt. `backend = "anthropic"` with `claude-haiku-4-5` under
  `[screener]` is the quality option at about a tenth of a cent per posting.
- Open the dashboard at `localhost`. `next dev` refuses its own client resources
  from an unrecognised host and leaves the page rendered but dead;
  `allowedDevOrigins` covers `127.0.0.1` as well.
- The title pre-filter is regexes, so it only knows the languages it was written
  for. It covers English and French (Lyft posts its Montreal internships as
  "Stagiaire ... l'été 2027"); a board posting in another language needs its own
  pattern. The patterns are pinned by tests against real titles.
- The spec's dedupe key collapses distinct postings with the same company,
  title and location. The first eleven-board scout logged 53 such collisions,
  none of them internships; each is recorded as a `posting.dedupe_collision`
  event.

## Tests

```bash
uv run pytest
uv run ruff check .
cd dashboard && npm test
```

CI runs all of it, plus the dashboard's typecheck and production build, on every
push to `main` and every pull request. The dashboard tests cover the review
page's logic (excerpt matching, bullet diffing, posting age, clipboard text)
using Node's built-in test runner, so no test framework is installed.

The suite makes no network calls and spends no money. It covers every stop
condition against hand-built critique histories, including a blocker overriding
a passing score; dedupe across runs with varied posting text; malformed model
JSON degrading without crashing the loop; the loop over real captured posting
text; and record-then-replay reproducing a report exactly. Two opt-in live tests
call the real API:

```bash
INTERNSHIP_AGENT_LIVE=1 uv run pytest tests/test_live_writer.py tests/test_live_loop.py -s
```
