"""Streamlit dashboard for browsing daily reports + leaving feedback.

Run with:

    paper-inbox dashboard

It expects the same configs as the CLI (``configs/runtime.yaml`` etc.). The
dashboard is **read + light-write**: it reads scores / briefs from SQLite and
filesystem, and lets you submit feedback rows via a button. It does *not*
trigger the pipeline — keep that side of the workflow in the CLI / cron.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st  # type: ignore[import-not-found]

from paper_inbox.db import (
    get_enrichment,
    list_briefs_for_date,
    list_scores_for_date,
    session,
)
from paper_inbox.pipeline.feedback import record_feedback
from paper_inbox.scoring.feedback_signals import derive_signals
from paper_inbox.settings import load_settings
from paper_inbox.storage.paths import RuntimePaths


@st.cache_data(ttl=30)
def _load_paths() -> RuntimePaths:
    settings = load_settings()
    return RuntimePaths.from_config(settings.runtime)


def _list_run_dates(reports_dir: Path) -> list[str]:
    if not reports_dir.exists():
        return []
    dates = [p.name for p in reports_dir.iterdir() if p.is_dir()]
    return sorted(dates, reverse=True)


def _bucket_emoji(bucket: str) -> str:
    return {
        "Must Read": "🔥",
        "Skim": "👀",
        "Archive": "📦",
        "Ignore": "🗑️",
    }.get(bucket, "·")


def render_paper_card(row: dict, paths: RuntimePaths, run_date: str) -> None:
    canonical_id = row["canonical_id"]
    bucket = row["bucket"]
    title = row["title"]
    priority = row["final_priority"]
    reasons = json.loads(row.get("reasons_json") or "[]")
    action = row.get("recommended_action") or "(none)"
    landing = row.get("landing_url")

    header = f"{_bucket_emoji(bucket)} **{title}**"
    sub = f"`{canonical_id}` · priority **{priority}/100** · bucket **{bucket}**"

    with st.container(border=True):
        st.markdown(header)
        st.markdown(sub)

        with session(paths.db_path) as conn:
            enrichment = get_enrichment(conn, int(row["paper_id"]))
        if enrichment:
            bits = []
            if enrichment.get("citation_count") is not None:
                bits.append(f"🔁 cites {enrichment['citation_count']}")
            if enrichment.get("influential_citation_count") is not None:
                bits.append(f"⭐ infl {enrichment['influential_citation_count']}")
            if enrichment.get("hf_upvotes"):
                bits.append(f"👍 HF {enrichment['hf_upvotes']}")
            if enrichment.get("venue"):
                bits.append(f"🏛 {enrichment['venue']}")
            if bits:
                st.caption("  ·  ".join(bits))
            if enrichment.get("tldr"):
                st.info(enrichment["tldr"])

        if reasons:
            with st.expander("Why this score"):
                for r in reasons:
                    st.markdown(f"- {r}")
        st.markdown(f"**建议动作**：{action}")

        if landing:
            st.markdown(f"[arXiv landing]({landing})")

        # Brief preview (collapsible, reads file directly to keep DB query light)
        from paper_inbox.storage.artifacts import (
            brief_path,
            file_id_from_canonical,
            report_brief_path,
        )

        possible_paths = [
            brief_path(paths.briefs_dir, canonical_id, run_date),
            report_brief_path(paths.reports_dir, run_date, bucket, canonical_id),
        ]
        brief_text = None
        for p in possible_paths:
            if p.exists():
                brief_text = p.read_text(encoding="utf-8")
                break
        if brief_text:
            with st.expander("View paper brief"):
                st.markdown(brief_text)
        else:
            st.caption("(brief not generated for this paper)")

        # Feedback buttons
        cols = st.columns(5)
        feedback_kinds = [
            ("👍 Useful", "useful"),
            ("🔥 Must read", "must_read"),
            ("📦 Archive", "archive"),
            ("👎 Not relevant", "not_relevant"),
            ("🌫 Too shallow", "too_shallow"),
        ]
        key_prefix = f"{canonical_id}_{file_id_from_canonical(canonical_id)}"
        for i, (label, kind) in enumerate(feedback_kinds):
            if cols[i].button(label, key=f"{key_prefix}_{kind}"):
                with session(paths.db_path) as conn:
                    fid = record_feedback(conn, canonical_id=canonical_id, feedback=kind)
                if fid is None:
                    st.error("Paper not found in DB.")
                else:
                    st.success(f"Recorded `{kind}` (feedback id={fid})")


def main() -> None:
    st.set_page_config(page_title="Paper Inbox", page_icon="📚", layout="wide")
    st.title("📚 Paper Inbox")

    try:
        paths = _load_paths()
    except FileNotFoundError as exc:
        st.error(f"Config not found: {exc}\n\nRun `paper-inbox init` first.")
        return

    dates = _list_run_dates(paths.reports_dir)
    if not dates:
        st.warning(
            "No daily reports yet. Run `paper-inbox run-daily` (or with "
            "`--mock-llm --offline-fixture tests/fixtures/sample_arxiv_feed.xml` for a dry run)."
        )
        return

    with st.sidebar:
        st.header("Filters")
        run_date = st.selectbox("Run date", dates, index=0)
        bucket_filter = st.multiselect(
            "Buckets",
            ["Must Read", "Skim", "Archive", "Ignore"],
            default=["Must Read", "Skim"],
        )
        search = st.text_input("Search title/abstract substring")

        with session(paths.db_path) as conn:
            sig = derive_signals(conn)
        st.divider()
        st.caption(
            f"Feedback so far: 👍 {sig.positive_count} · 👎 {sig.negative_count}"
        )
        if sig.positive_keywords:
            st.caption(
                "Positive keywords (top): "
                + ", ".join(sorted(sig.positive_keywords)[:10])
            )

    with session(paths.db_path) as conn:
        rows = list_scores_for_date(conn, run_date)
        briefs = {b["paper_id"] for b in list_briefs_for_date(conn, run_date)}

    if bucket_filter:
        rows = [r for r in rows if r["bucket"] in bucket_filter]
    if search:
        s = search.lower()
        rows = [
            r for r in rows
            if s in (r["title"] or "").lower() or s in (r.get("abstract") or "").lower()
        ]

    counts = {b: 0 for b in ["Must Read", "Skim", "Archive", "Ignore"]}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1

    cols = st.columns(5)
    cols[0].metric("Total", len(rows))
    cols[1].metric("🔥 Must Read", counts.get("Must Read", 0))
    cols[2].metric("👀 Skim", counts.get("Skim", 0))
    cols[3].metric("📦 Archive", counts.get("Archive", 0))
    cols[4].metric("📝 Briefs", len(briefs))

    daily_brief = paths.reports_dir / run_date / "daily_brief.md"
    if daily_brief.exists():
        with st.expander(f"📰 Daily brief for {run_date}", expanded=False):
            st.markdown(daily_brief.read_text(encoding="utf-8"))

    st.markdown(f"### {len(rows)} papers")
    for row in rows:
        render_paper_card(row, paths, run_date)
        st.write("")  # vertical spacing


if __name__ == "__main__":
    main()
