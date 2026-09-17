-- Initial schema. All timestamps are ISO 8601 UTC text.
--
-- Chain: postings -> applications -> drafts -> critiques -> critique_scores.
-- round_index lives on both drafts and critiques so the core analysis
-- ("mean overall by round across all applications") is a single GROUP BY
-- on critiques with no joins.

CREATE TABLE postings (
    id             INTEGER PRIMARY KEY,
    dedupe_hash    TEXT NOT NULL UNIQUE,      -- sha256(normalized company|title|location)
    source         TEXT NOT NULL,             -- e.g. 'greenhouse:scaleai'
    external_id    TEXT,                      -- the board's own job id, if any
    company        TEXT NOT NULL,
    title          TEXT NOT NULL,
    location       TEXT,
    url            TEXT NOT NULL,
    description    TEXT,                      -- plain text, HTML stripped
    content_hash   TEXT,                      -- sha256(description); detects edits
    raw_json       TEXT,                      -- original payload, for reprocessing
    posted_at      TEXT,                      -- board-reported publish time
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'dismissed', 'expired'))
);

-- Same board + same job id must map to one row. NULL external_ids are
-- distinct in SQLite, so sources without ids are unaffected.
CREATE UNIQUE INDEX idx_postings_source_external ON postings (source, external_id);

CREATE TABLE screenings (
    id          INTEGER PRIMARY KEY,
    posting_id  INTEGER NOT NULL REFERENCES postings (id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    fit_score   REAL NOT NULL,
    reason      TEXT NOT NULL,
    raw_json    TEXT,
    created_at  TEXT NOT NULL
);
-- A posting may be screened more than once (new criteria, new resume); latest wins.
CREATE INDEX idx_screenings_posting ON screenings (posting_id, created_at);

CREATE TABLE applications (
    id          INTEGER PRIMARY KEY,
    posting_id  INTEGER NOT NULL REFERENCES postings (id) ON DELETE CASCADE,
    status      TEXT NOT NULL
                CHECK (status IN ('drafting', 'awaiting_review', 'approved',
                                  'rejected', 'submitted')),
    -- 'submitted' is set by hand after the human submits. Nothing here submits.
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_applications_posting ON applications (posting_id);

CREATE TABLE drafts (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    round_index     INTEGER NOT NULL CHECK (round_index >= 0),  -- 0 = first draft
    bullets_json    TEXT NOT NULL,            -- JSON array of tailored resume bullets
    cover_letter    TEXT NOT NULL,
    writer_model    TEXT,
    usage_json      TEXT,                     -- token counts etc., for cost tracking
    created_at      TEXT NOT NULL,
    UNIQUE (application_id, round_index)
);

CREATE TABLE critiques (
    id                       INTEGER PRIMARY KEY,
    draft_id                 INTEGER NOT NULL UNIQUE REFERENCES drafts (id) ON DELETE CASCADE,
    round_index              INTEGER NOT NULL CHECK (round_index >= 0),
    overall                  REAL NOT NULL,
    verdict                  TEXT NOT NULL CHECK (verdict IN ('accept', 'revise')),
    unsupported_claim_count  INTEGER NOT NULL,
    critique_json            TEXT NOT NULL,   -- full Critique.model_dump_json(); source of truth
    critic_model             TEXT,
    usage_json               TEXT,
    created_at               TEXT NOT NULL
);
CREATE INDEX idx_critiques_round ON critiques (round_index);

-- Per-dimension scores unpacked from critique_json so "mean score per
-- dimension per round" is also one query rather than JSON extraction.
CREATE TABLE critique_scores (
    critique_id  INTEGER NOT NULL REFERENCES critiques (id) ON DELETE CASCADE,
    dimension    TEXT NOT NULL
                 CHECK (dimension IN ('grounding', 'coverage', 'specificity',
                                      'density', 'voice')),
    score        INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    reason       TEXT NOT NULL,
    PRIMARY KEY (critique_id, dimension)
);

-- Flat event log. Nullable FKs so one table covers every loop.
CREATE TABLE events (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,
    kind            TEXT NOT NULL,            -- dotted: 'scout.run_finished', 'critic.scored', ...
    posting_id      INTEGER REFERENCES postings (id) ON DELETE SET NULL,
    application_id  INTEGER REFERENCES applications (id) ON DELETE SET NULL,
    draft_id        INTEGER REFERENCES drafts (id) ON DELETE SET NULL,
    payload_json    TEXT
);
CREATE INDEX idx_events_kind_ts ON events (kind, ts);
