-- Human edits are persisted as ordinary draft rows, so the review UI and the
-- eval harness read one table and one ordering. They must never be counted as
-- model output, hence the attribution column rather than a separate table:
-- a separate table would need a union everywhere a draft is read, and would
-- still have to answer "which round came first".
--
-- Existing rows were all written by the Writer, so 'writer' is the default.

ALTER TABLE drafts ADD COLUMN authored_by TEXT NOT NULL DEFAULT 'writer'
    CHECK (authored_by IN ('writer', 'human'));
