"""Generate the daily Markdown report."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path

from jinja2 import Template

from paper_inbox.db import list_briefs_for_date, list_scores_for_date
from paper_inbox.models import PaperBucket
from paper_inbox.storage.artifacts import file_id_from_canonical, report_brief_path

logger = logging.getLogger(__name__)


DAILY_REPORT_TEMPLATE = """# Daily Paper Inbox - {{ run_date }}

## Summary

今天共扫描 {{ total_count }} 篇论文。

- Must Read: {{ must_read_count }}
- Skim: {{ skim_count }}
- Archive: {{ archive_count }}
- Ignore: {{ ignore_count }}

## 今日趋势

{% if trends -%}
{% for t in trends -%}
- {{ t }}
{% endfor %}
{%- else %}
- (今日没有显著趋势 — 候选过少)
{% endif %}

## Must Read

{% if must_read -%}
{% for p in must_read -%}
### {{ loop.index }}. {{ p.title }}

Priority: {{ p.priority }}/100  ·  Source: {{ p.source }}:{{ p.source_id }}

为什么值得读：
{% for r in p.reasons -%}
- {{ r }}
{% endfor %}

建议动作：{{ p.recommended_action or "(none)" }}

{% if p.brief_path -%}
Brief: {{ p.brief_path }}
{%- endif %}

{% if p.landing_url -%}
Landing: {{ p.landing_url }}
{%- endif %}

---
{% endfor %}
{%- else %}
(无)
{% endif %}

## Skim

{% if skim -%}
{% for p in skim -%}
### {{ p.title }}

Priority: {{ p.priority }}/100  ·  Source: {{ p.source }}:{{ p.source_id }}

为什么值得扫：
{% for r in p.reasons -%}
- {{ r }}
{% endfor %}

建议只读：{{ p.recommended_action or "(none)" }}

{% if p.brief_path -%}
Brief: {{ p.brief_path }}
{%- endif %}

---
{% endfor %}
{%- else %}
(无)
{% endif %}

## Archive Highlights

{% if archive -%}
{% for p in archive -%}
- **{{ p.title }}** ({{ p.priority }}/100) — {{ p.recommended_action or "归档" }}
{% endfor %}
{%- else %}
(无)
{% endif %}

## 建议明天跟进

{% if follow_up -%}
{% for f in follow_up -%}
- {{ f }}
{% endfor %}
{%- else %}
- (无明确跟进项)
{% endif %}
"""


def _row_to_card(row: dict, briefs_index: dict[int, dict], reports_dir: Path, run_date: str) -> dict:
    paper_pk = row["paper_id"]
    brief = briefs_index.get(paper_pk)
    canonical_id = row["canonical_id"]
    bucket = row["bucket"]
    if brief is not None:
        rp = report_brief_path(reports_dir, run_date, bucket, canonical_id)
        try:
            brief_path_str = str(rp.relative_to(Path.cwd()))
        except ValueError:
            brief_path_str = str(rp)
    else:
        brief_path_str = None
    reasons = json.loads(row.get("reasons_json") or "[]")
    return {
        "title": row["title"],
        "priority": row["final_priority"],
        "bucket": bucket,
        "source": row["source"],
        "source_id": row["source_id"],
        "reasons": reasons,
        "recommended_action": row["recommended_action"],
        "landing_url": row.get("landing_url"),
        "brief_path": brief_path_str,
        "file_id": file_id_from_canonical(canonical_id),
        "canonical_id": canonical_id,
    }


def _detect_trends(cards: list[dict]) -> list[str]:
    if not cards:
        return []
    cat_counter: Counter[str] = Counter()
    for c in cards:
        for cat in (c.get("categories") or "").split(","):
            cat = cat.strip()
            if cat:
                cat_counter[cat] += 1
    out: list[str] = []
    for cat, count in cat_counter.most_common(3):
        if count >= 2:
            out.append(f"{cat} 方向今日候选 {count} 篇")
    return out


def generate_daily_report(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    reports_dir: Path,
) -> Path:
    """Render the daily report Markdown and write it to the reports dir."""
    rows = list_scores_for_date(conn, run_date)
    briefs = list_briefs_for_date(conn, run_date)
    briefs_index = {b["paper_id"]: b for b in briefs}

    cards = [_row_to_card(r, briefs_index, reports_dir, run_date) for r in rows]

    bucket_counts = Counter(c["bucket"] for c in cards)
    must_read = [c for c in cards if c["bucket"] == PaperBucket.MUST_READ.value]
    skim = [c for c in cards if c["bucket"] == PaperBucket.SKIM.value]
    archive = [c for c in cards if c["bucket"] == PaperBucket.ARCHIVE.value]

    follow_up: list[str] = []
    if must_read:
        follow_up.append(f"完成 {len(must_read)} 篇 Must Read 的精读")
    if skim:
        follow_up.append(f"对 {len(skim)} 篇 Skim 论文做 30 分钟快速过一遍")

    tpl = Template(DAILY_REPORT_TEMPLATE)
    md = tpl.render(
        run_date=run_date,
        total_count=len(cards),
        must_read_count=bucket_counts.get(PaperBucket.MUST_READ.value, 0),
        skim_count=bucket_counts.get(PaperBucket.SKIM.value, 0),
        archive_count=bucket_counts.get(PaperBucket.ARCHIVE.value, 0),
        ignore_count=bucket_counts.get(PaperBucket.IGNORE.value, 0),
        trends=_detect_trends(cards),
        must_read=must_read,
        skim=skim,
        archive=archive[:10],
        follow_up=follow_up,
    )

    out_dir = reports_dir / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "daily_brief.md"
    out_path.write_text(md, encoding="utf-8")
    logger.info("[report] wrote %s", out_path)
    return out_path
