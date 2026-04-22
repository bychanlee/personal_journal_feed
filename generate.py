#!/usr/bin/env python3
"""
Journal Digest — Fetch papers from journals (arXiv, APS, Science, Nature, ...),
score with Claude Haiku, generate static HTML for GitHub Pages.

Usage:
    python generate.py                          # uses config.yaml in same dir
    python generate.py --config /path/to/config.yaml
    ANTHROPIC_API_KEY=sk-... python generate.py  # required for Haiku scoring
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from time import mktime

import certifi
import ssl
import feedparser
import yaml

# Fix SSL certificate verification on macOS
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

# Prevent feedparser from hanging on unresponsive feeds (e.g. SemiEng)
socket.setdefaulttimeout(30)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Data ─────────────────────────────────────────────────────

@dataclass
class Paper:
    title: str
    authors: str
    last_author: str
    abstract: str
    url: str
    paper_id: str           # arXiv ID or DOI
    feed_name: str          # e.g. "PRB", "cond-mat.mtrl-sci"
    feed_category: str      # e.g. "APS", "arXiv", "AAAS", "Nature"
    published: str          # ISO
    score: int = 0          # 1–5 (5 = most relevant)
    reason: str = ""
    matched: list[str] = field(default_factory=list)


# ── Config ───────────────────────────────────────────────────

def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Fetch ────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_paper_id(url: str) -> str:
    """Extract arXiv ID or DOI from URL."""
    m = re.search(r"(\d{4}\.\d{4,5})", url)
    if m:
        return m.group(1)
    m = re.search(r"(10\.\d{4,}/[^\s]+)", url)
    if m:
        return m.group(1)
    return ""


def _last_author(authors: str) -> str:
    parts = [a.strip() for a in authors.split(",") if a.strip()]
    last = parts[-1] if parts else ""
    if last.lower().startswith("and "):
        last = last[4:].strip()
    return last


def fetch_feed(url: str, name: str, category: str) -> list[Paper]:
    """Fetch papers from a single RSS/Atom feed."""
    papers = []
    try:
        d = feedparser.parse(url)
    except Exception as exc:
        logger.error(f"Failed to parse feed {name} ({url}): {exc}")
        return []

    for e in d.entries:
        title = _clean(e.get("title", ""))
        if not title:
            continue

        authors_raw = ""
        if e.get("authors"):
            authors_list = e["authors"]
            authors_raw = ", ".join(
                (a.get("name", "") if isinstance(a, dict) else str(a))
                for a in authors_list
                if (a.get("name") if isinstance(a, dict) else a)
            )
        elif e.get("author"):
            authors_raw = _clean(e["author"])
        if not authors_raw and e.get("author_detail", {}).get("name"):
            authors_raw = e["author_detail"]["name"]

        abstract = ""
        if e.get("summary"):
            abstract = _clean(e["summary"])[:2000]
        elif e.get("description"):
            abstract = _clean(e["description"])[:2000]
        elif e.get("content"):
            abstract = _clean(e["content"][0].get("value", ""))[:2000]

        url_ = e.get("link", "")
        if not url_:
            continue

        pub = ""
        for fld in ("published_parsed", "updated_parsed"):
            tp = e.get(fld)
            if tp:
                try:
                    pub = datetime.fromtimestamp(mktime(tp), tz=timezone.utc).isoformat()
                except Exception:
                    pass
                break

        papers.append(Paper(
            title=title, authors=authors_raw,
            last_author=_last_author(authors_raw),
            abstract=abstract, url=url_,
            paper_id=_extract_paper_id(url_),
            feed_name=name, feed_category=category,
            published=pub,
        ))

    logger.info(f"  {name}: {len(papers)} entries")
    return papers


def _is_recent(pub: str, max_age_days: int = 2) -> bool:
    """Return True if published within max_age_days."""
    if not pub:
        return True  # keep papers with no date info
    try:
        dt = datetime.fromisoformat(pub)
        age = datetime.now(timezone.utc) - dt
        return age.total_seconds() < max_age_days * 86400
    except Exception:
        return True


def fetch_all(config: dict) -> list[Paper]:
    """Fetch from all feeds, deduplicated, filtered to recent papers only."""
    max_age = config.get("output", {}).get("max_age_days", 2)
    all_papers, seen = [], set()
    for f in config.get("feeds", []):
        feed_cat = f.get("category", f["name"])
        logger.info(f"Fetching {f['name']} ({feed_cat})...")
        for p in fetch_feed(f["url"], f["name"], feed_cat):
            if not _is_recent(p.published, max_age):
                continue
            h = hashlib.sha256(
                (p.title.lower() + "|" + p.last_author.lower()).encode()
            ).hexdigest()[:16]
            if h not in seen:
                seen.add(h)
                all_papers.append(p)
    logger.info(f"Total unique papers: {len(all_papers)} (within {max_age} days)")
    return all_papers


# ── Claude CLI scoring (1–5 scale) ───────────────────────────

def score_batch(
    profile_text: str,
    papers: list[tuple[int, Paper]],
) -> dict[int, tuple[int, str]]:
    """Score a batch via Claude Code CLI. Returns {index: (score, reason)}."""
    lines = []
    for idx, p in papers:
        abs_snippet = re.sub(r"\s+", " ", p.abstract or "").strip()[:300]
        line = f"{idx}. {p.title} [{p.feed_name}]"
        if abs_snippet:
            line += f"\n   abstract: {abs_snippet}"
        lines.append(line)

    prompt = (
        "You are an academic paper relevance scorer for a condensed-matter physicist.\n\n"
        f"User research profile:\n{profile_text}\n\n"
        "METHOD GATE (apply BEFORE topic match):\n"
        "First decide the paper's class from title + abstract:\n"
        "  (E) Experimental measurement / device fabrication / synthesis on a real\n"
        "      material — score by topic relevance to R1~R3 materials. Gate does NOT\n"
        "      apply. Real experiments on relevant materials can still score 4-5.\n"
        "  (T) Theory or computation — apply the gate below.\n"
        "  (I) Industry / infrastructure trend (HPC, GPU, EDA, AI) — gate does NOT\n"
        "      apply, score by relevance to landing paths.\n\n"
        "Theory gate: if the paper is class (T) AND its primary method is\n"
        "  - model Hamiltonian (Dirac / k·p / tight-binding / Hubbard toy model),\n"
        "  - transfer matrix / NEGF on toy models,\n"
        "  - analytical superlattice / barrier transport without ab initio input,\n"
        "  - phenomenological spintronics / valleytronics device modeling,\n"
        "then CAP the score at 2 regardless of material or topic keywords. For class\n"
        "(T) papers, the (GW-BSE) / (EPW) / (ab initio) qualifiers in the topic list\n"
        "are load-bearing — surface keywords alone ('TMD', 'WSe₂', 'valley',\n"
        "'magnetoresistance') do NOT lift a model-Hamiltonian theory paper into\n"
        "R1/R2 scope. For class (E) papers these qualifiers describe the parallel\n"
        "theory variant and are NOT a requirement.\n\n"
        "Score each article on a 1–5 integer scale for relevance:\n"
        "  5 = Directly in my research area, must read\n"
        "  4 = Closely related, likely useful\n"
        "  3 = Somewhat related, worth skimming\n"
        "  2 = Tangentially related (or capped by method gate)\n"
        "  1 = Not relevant\n"
        "Provide a one-line reason for scores >= 3.\n\n"
        "Articles:\n" + "\n".join(lines) + "\n\n"
        "Respond with ONLY a JSON array, no other text. Example:\n"
        '[{"index":1,"score":4,"reason":"..."}, {"index":2,"score":1}]'
    )

    try:
        result = subprocess.run(
            ["claude", "--print", "--model", "haiku"],
            input=prompt,
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"Claude CLI error (rc={result.returncode}): {result.stderr[:500]}")
            logger.error(f"Claude CLI stdout: {result.stdout[:500]}")
            return {}

        output = result.stdout.strip()
        # Extract JSON array from response
        match = re.search(r'\[.*\]', output, re.DOTALL)
        if not match:
            logger.error("No JSON array found in Claude CLI output")
            return {}

        scores_list = json.loads(match.group())
        results = {}
        for s in scores_list:
            if not isinstance(s, dict):
                continue
            idx = s.get("index")
            try:
                sc = max(1, min(5, int(s.get("score", 1))))
            except (TypeError, ValueError):
                continue
            reason = s.get("reason", "") or ""
            if idx is not None:
                results[idx] = (sc, reason)
        return results
    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timed out")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude CLI JSON: {e}")
    except Exception as e:
        logger.error(f"Claude CLI scoring error: {e}")
    return {}


def score_all(papers: list[Paper], config: dict) -> tuple[list[Paper], list[Paper]]:
    """Score papers. Returns (scored_papers, news_items)."""
    profile = config.get("profile", {})

    # Separate news from academic papers
    academic, news = [], []
    for p in papers:
        if p.feed_category == "News":
            news.append(p)
        else:
            academic.append(p)
    logger.info(f"Split: {len(academic)} academic, {len(news)} news")

    # Score all academic papers via Claude CLI
    if academic:
        profile_parts = [profile.get("text", "")]
        high = profile.get("high_priority_topics", [])
        if high:
            profile_parts.append("\nHigh-priority topics (score 4-5):\n" + "\n".join(f"- {t}" for t in high))
        bg = profile.get("background_topics", [])
        if bg:
            profile_parts.append("\nBackground topics (score 2-3):\n" + "\n".join(f"- {t}" for t in bg))
        methods = profile.get("methods", [])
        if methods:
            profile_parts.append("\nMethods/tools I use: " + ", ".join(methods))
        profile_text = "\n".join(profile_parts)
        batch_size = 60
        indexed = list(enumerate(academic, 1))
        scored_count = 0
        for i in range(0, len(indexed), batch_size):
            batch = indexed[i:i + batch_size]
            logger.info(f"Scoring batch {i // batch_size + 1} ({len(batch)} papers)...")
            results = score_batch(profile_text, batch)
            for idx, p in batch:
                if idx in results:
                    p.score, p.reason = results[idx]
                    scored_count += 1
        if scored_count == 0:
            logger.warning("Claude CLI returned no results — using keyword fallback")
            academic = _keyword_fallback_list(academic, profile)

    threshold = config.get("output", {}).get("score_threshold", 2)
    scored = [p for p in academic if p.score >= threshold]
    scored.sort(key=lambda p: (-p.score, p.title))
    logger.info(f"Papers above threshold ({threshold}): {len(scored)}")
    return scored, news


def _keyword_fallback_list(papers: list[Paper], profile: dict) -> list[Paper]:
    """Simple keyword scoring when no API key is available."""
    core = [kw.lower() for kw in profile.get("core_interests", [])]
    methods = [kw.lower() for kw in profile.get("methods", [])]
    emerging = [kw.lower() for kw in profile.get("emerging_interests", [])]

    for p in papers:
        text = (p.title + " " + p.abstract).lower()
        raw = 0.0
        m = []
        for kw in core:
            if kw in text:
                raw += 0.15
                m.append(kw)
        for kw in methods:
            if kw in text:
                raw += 0.08
                m.append(kw)
        for kw in emerging:
            if kw in text:
                raw += 0.06
                m.append(kw)
        p.score = max(1, min(5, round(raw * 5 + 0.5)))
        p.matched = m
        p.reason = f"Keyword matches: {', '.join(m[:5])}" if m else ""
    return papers


# ── HTML generation ──────────────────────────────────────────

def generate_html(papers: list[Paper], news: list[Paper], config: dict, date_str: str) -> str:
    all_papers = papers

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def score_badge(s: int) -> str:
        return str(s)

    def score_color(s: int) -> str:
        if s >= 5:
            return "#ef4444"
        if s >= 4:
            return "#f97316"
        if s >= 3:
            return "#eab308"
        return "#6b7280"

    def time_ago(pub: str) -> str:
        if not pub:
            return ""
        try:
            dt = datetime.fromisoformat(pub)
            diff = datetime.now(timezone.utc) - dt
            hours = int(diff.total_seconds() / 3600)
            if hours < 1:
                return "now"
            if hours < 24:
                return f"{hours}h"
            return f"{hours // 24}d"
        except Exception:
            return ""

    # Group by feed_name, sorted by score within each group
    from itertools import groupby
    grouped = sorted(all_papers, key=lambda p: (p.feed_name, -p.score))

    rows = []
    for feed_name, group in groupby(grouped, key=lambda p: p.feed_name):
        rows.append(f'<tr class="group-header"><td colspan="4">{_esc(feed_name)}</td></tr>')
        for p in group:
            rows.append(
                f'<tr>'
                f'<td class="td-score">'
                f'<span class="score-num" style="background:{score_color(p.score)}">{score_badge(p.score)}</span></td>'
                f'<td class="td-title">'
                f'<a href="{p.url}" target="_blank" rel="noopener">{_esc(p.title)}</a>'
                f'{"<br><span class=reason>" + _esc(p.reason) + "</span>" if p.reason else ""}'
                f'</td>'
                f'<td class="td-authors">{_esc(p.authors)}</td>'
                f'<td class="td-time">{time_ago(p.published)}</td>'
                f'</tr>'
            )

    # Feed summary for header
    feed_counts: dict[str, int] = {}
    for p in all_papers:
        feed_counts[p.feed_name] = feed_counts.get(p.feed_name, 0) + 1
    feed_summary = " · ".join(f"{n} ({c})" for n, c in sorted(feed_counts.items()))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Journal Digest — {date_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Inter', system-ui, sans-serif;
    background: #ffffff;
    color: #1f2328;
    line-height: 1.5;
    padding: 24px 32px;
    max-width: 1400px;
    margin: 0 auto;
  }}

  /* Site title */
  .site-title {{
    font-size: 14px;
    font-weight: 500;
    color: #656d76;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    margin-bottom: 16px;
  }}

  /* Header */
  .page-header {{
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid #d1d9e0;
  }}
  .page-header h1 {{
    font-size: 24px;
    font-weight: 600;
    margin-bottom: 4px;
  }}
  .page-header .meta {{
    font-size: 15px;
    color: #656d76;
  }}
  .page-header .feeds {{
    font-size: 13px;
    color: #848d97;
    margin-top: 4px;
  }}

  /* Table */
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 15px;
  }}
  thead th {{
    text-align: left;
    font-size: 13px;
    font-weight: 500;
    text-transform: uppercase;
    color: #848d97;
    padding: 10px 12px;
    border-bottom: 2px solid #d1d9e0;
    position: sticky;
    top: 0;
    background: #ffffff;
    cursor: pointer;
    user-select: none;
  }}
  thead th:hover {{ color: #1f2328; }}
  tbody tr {{
    border-bottom: 1px solid #eaeef2;
    transition: background 0.1s;
  }}
  tbody tr:hover {{ background: #f6f8fa; }}
  td {{ padding: 10px 12px; vertical-align: top; }}

  .td-score {{
    width: 48px;
    text-align: center;
  }}
  .score-num {{
    display: inline-block;
    width: 28px;
    height: 28px;
    line-height: 28px;
    text-align: center;
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 14px;
    font-weight: 600;
    color: #fff;
  }}

  .td-title a {{
    color: #0969da;
    text-decoration: none;
    font-weight: 500;
    font-size: 15px;
  }}
  .td-title a:hover {{ text-decoration: underline; }}
  .reason {{
    font-size: 13px;
    color: #848d97;
    display: block;
    margin-top: 3px;
  }}

  .td-authors {{
    color: #656d76;
    font-size: 14px;
    max-width: 350px;
  }}

  .td-time {{
    width: 50px;
    text-align: right;
    color: #848d97;
    font-size: 12px;
    white-space: nowrap;
  }}

  .empty {{
    text-align: center;
    padding: 60px;
    color: #848d97;
  }}

  .group-header td {{
    font-weight: 600;
    font-size: 14px;
    padding: 14px 12px 6px;
    color: #1f2328;
    border-bottom: 2px solid #d1d9e0;
    background: #f6f8fa;
    letter-spacing: 0.3px;
  }}

  /* News section */
  .news-section {{
    margin-top: 32px;
    padding-top: 24px;
    border-top: 1px solid #d1d9e0;
  }}
  .news-section h2 {{
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 12px;
  }}
  .news-item {{
    padding: 8px 0;
    border-bottom: 1px solid #eaeef2;
    font-size: 15px;
  }}
  .news-item a {{
    color: #0969da;
    text-decoration: none;
    font-weight: 500;
  }}
  .news-item a:hover {{ text-decoration: underline; }}
  .news-source {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    background: #f6f8fa;
    padding: 2px 6px;
    border-radius: 4px;
    color: #656d76;
    border: 1px solid #eaeef2;
    margin-right: 6px;
  }}
  .news-time {{
    color: #848d97;
    font-size: 12px;
    margin-left: 8px;
  }}

  /* Responsive */
  @media (max-width: 768px) {{
    body {{ padding: 16px; }}
    .td-authors {{ max-width: 150px; }}
    .td-time {{ display: none; }}
  }}
</style>
</head>
<body>

<div class="site-title">Byeongchan Lee's Personal Journal Feed</div>

<div class="page-header">
  <h1>Journal Digest</h1>
  <div class="meta">{date_str} — {len(all_papers)} papers scored by Haiku</div>
  <div class="feeds">{feed_summary}</div>
</div>


<table>
<thead>
  <tr>
    <th>Score</th>
    <th>Title</th>
    <th>Authors</th>
    <th style="text-align:right">Time</th>
  </tr>
</thead>
<tbody id="tbody">
{"".join(rows) if rows else '<tr><td colspan="5" class="empty">No relevant papers found today.</td></tr>'}
</tbody>
</table>

{"" if not news else f'''
<div class="news-section">
  <h2>Tech News</h2>
  <div class="news-list">
    {"".join(
        f'<div class="news-item">'
        f'<code class="news-source">{_esc(n.feed_name)}</code> '
        f'<a href="{n.url}" target="_blank" rel="noopener">{_esc(n.title)}</a>'
        f'<span class="news-time">{time_ago(n.published)}</span>'
        f'</div>'
        for n in news
    )}
  </div>
</div>
'''}

</body>
</html>"""
    return html


# ── Output helpers ───────────────────────────────────────────

def save_json(papers: list[Paper], path: Path):
    data = [asdict(p) for p in papers]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "papers": data}, f, indent=2, ensure_ascii=False)


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Journal Digest → Haiku → HTML")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config.yaml")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "index.html")
    args = parser.parse_args()

    config = load_config(args.config)
    date_str = datetime.now().strftime("%Y-%m-%d")

    papers = fetch_all(config)
    if not papers:
        logger.warning("No papers fetched — generating empty page")

    scored, news = score_all(papers, config)

    html = generate_html(scored, news, config, date_str)
    args.output.write_text(html, encoding="utf-8")
    logger.info(f"HTML written to {args.output}")

    json_path = args.output.parent / "latest.json"
    save_json(scored + news, json_path)
    logger.info(f"JSON written to {json_path}")


if __name__ == "__main__":
    main()
