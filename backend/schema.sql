-- Livetime 時光機 — SQLite Schema
-- Phase 1: core tables mirroring the frontend data model

-- ── Event types (learn / work / intern / job / life) ──────────────────────
CREATE TABLE IF NOT EXISTS event_types (
    key     TEXT PRIMARY KEY,
    label   TEXT NOT NULL,
    icon    TEXT NOT NULL,
    color   TEXT NOT NULL
);

-- ── Momentum / mood micro-badges ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS momentum_types (
    key     TEXT PRIMARY KEY,
    label   TEXT NOT NULL,
    icon    TEXT NOT NULL,
    color   TEXT NOT NULL,
    soft    TEXT NOT NULL    -- rgba string for background tint
);

-- ── Timeline events (main table) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id          TEXT    PRIMARY KEY,        -- e.g. "e1"
    title       TEXT    NOT NULL,
    date_label  TEXT    NOT NULL,           -- display string, e.g. "2026 · 3月"
    date_sort   INTEGER NOT NULL,           -- sortable yyyymm, e.g. 202603
    year        INTEGER GENERATED ALWAYS AS (date_sort / 100) VIRTUAL,
    month       INTEGER GENERATED ALWAYS AS (date_sort % 100) VIRTUAL,
    type        TEXT    NOT NULL REFERENCES event_types(key),
    momentum    TEXT    REFERENCES momentum_types(key),
    description TEXT,
    has_media   INTEGER NOT NULL DEFAULT 0, -- boolean 0/1
    link        TEXT,                       -- optional URL
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Tags (many-to-many) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS event_tags (
    event_id  TEXT    NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (event_id, tag_id)
);

-- ── Monthly mood / productivity series ───────────────────────────────────
CREATE TABLE IF NOT EXISTS monthly_series (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    month   TEXT    NOT NULL UNIQUE, -- "23/05" format for display
    yyyymm  INTEGER NOT NULL UNIQUE, -- 202305 for sorting
    mood    INTEGER NOT NULL CHECK(mood BETWEEN 0 AND 100),
    prod    INTEGER NOT NULL CHECK(prod BETWEEN 0 AND 100)
);

-- ── OKR board ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS okrs (
    id      TEXT    PRIMARY KEY,
    season  TEXT    NOT NULL,
    obj     TEXT    NOT NULL,   -- objective
    color   TEXT
);

CREATE TABLE IF NOT EXISTS key_results (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    okr_id    TEXT    NOT NULL REFERENCES okrs(id) ON DELETE CASCADE,
    title     TEXT    NOT NULL,
    progress  INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- ── Skill radar ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT    NOT NULL UNIQUE,
    value INTEGER NOT NULL CHECK(value BETWEEN 0 AND 100)
);

-- ── Indexes ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_events_date_sort ON events(date_sort DESC);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_momentum  ON events(momentum);
CREATE INDEX IF NOT EXISTS idx_event_tags_event ON event_tags(event_id);
CREATE INDEX IF NOT EXISTS idx_event_tags_tag   ON event_tags(tag_id);
