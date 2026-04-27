"""SQLite schema and initial migration."""

from __future__ import annotations

import sqlite3

INIT_SQL = """
CREATE TABLE IF NOT EXISTS papers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_id TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  abstract TEXT,
  authors_json TEXT,
  published_at TEXT,
  updated_at TEXT,
  pdf_url TEXT,
  landing_url TEXT,
  categories_json TEXT,
  tags_json TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triage_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL,
  run_date TEXT NOT NULL,
  relevance_to_user INTEGER NOT NULL,
  novelty INTEGER NOT NULL,
  practicality INTEGER NOT NULL,
  experiment_strength INTEGER NOT NULL,
  reproducibility_signal INTEGER NOT NULL,
  trend_signal INTEGER NOT NULL,
  final_priority INTEGER NOT NULL,
  bucket TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  recommended_action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS paper_briefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL,
  run_date TEXT NOT NULL,
  brief_markdown TEXT NOT NULL,
  brief_json TEXT,
  model TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL,
  artifact_type TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS user_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL,
  feedback TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (paper_id) REFERENCES papers(id)
);

-- v0.2: Semantic Scholar enrichment cache (per paper, latest write wins).
CREATE TABLE IF NOT EXISTS paper_enrichment (
  paper_id INTEGER PRIMARY KEY,
  citation_count INTEGER,
  influential_citation_count INTEGER,
  tldr TEXT,
  year INTEGER,
  venue TEXT,
  hf_upvotes INTEGER,
  fetched_at TEXT NOT NULL,
  FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE INDEX IF NOT EXISTS idx_papers_canonical_id ON papers(canonical_id);
CREATE INDEX IF NOT EXISTS idx_scores_run_date ON triage_scores(run_date);
CREATE INDEX IF NOT EXISTS idx_scores_priority ON triage_scores(final_priority);
CREATE INDEX IF NOT EXISTS idx_feedback_paper ON user_feedback(paper_id);
"""


def init_database(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not exist."""
    conn.executescript(INIT_SQL)
    conn.commit()
