#!/usr/bin/env python3
"""Generate a compact, theme-aware GitHub stats SVG using only the Python stdlib."""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks {
          firstDay
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
    repositories(
      first: 100
      ownerAffiliations: OWNER
      isFork: false
      orderBy: { field: UPDATED_AT, direction: DESC }
    ) {
      nodes {
        name
        isPrivate
        languages(first: 10, orderBy: { field: SIZE, direction: DESC }) {
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
  }
}
"""

FALLBACK_COLORS = {
    "TypeScript": "#3178c6",
    "Python": "#3572A5",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "CSS": "#563d7c",
    "HTML": "#e34c26",
    "PLpgSQL": "#336790",
    "C#": "#178600",
    "Luau": "#00A2FF",
    "Shell": "#89e051",
}


def fetch_github_stats(username: str, token: str) -> dict:
    payload = json.dumps({"query": QUERY, "variables": {"login": username}}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "przish-profile-stats",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL error {exc.code}: {body}") from exc

    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {json.dumps(data['errors'])}")
    return data["data"]["user"]


def compute_languages(repos: list[dict], top_n: int = 4) -> list[tuple[str, float, str]]:
    totals: dict[str, int] = {}
    colors: dict[str, str] = {}
    for repo in repos:
        for edge in repo.get("languages", {}).get("edges", []):
            name = edge["node"]["name"]
            size = edge["size"]
            totals[name] = totals.get(name, 0) + size
            if name not in colors:
                colors[name] = edge["node"].get("color") or FALLBACK_COLORS.get(name, "#8b949e")

    grand_total = sum(totals.values())
    if grand_total == 0:
        return [("No code", 100.0, "#8b949e")]

    sorted_langs = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    result: list[tuple[str, float, str]] = []
    other_bytes = 0
    for idx, (name, size) in enumerate(sorted_langs):
        if idx < top_n:
            result.append((name, (size / grand_total) * 100.0, colors[name]))
        else:
            other_bytes += size
    if other_bytes > 0:
        result.append(("Other", (other_bytes / grand_total) * 100.0, "#6e7681"))
    return result


def level_class(count: int, max_count: int) -> int:
    if count <= 0:
        return 0
    if max_count <= 4:
        return min(4, count)
    ratio = count / max_count
    if ratio <= 0.25:
        return 1
    if ratio <= 0.50:
        return 2
    if ratio <= 0.75:
        return 3
    return 4


def render_skyline(weeks: list[dict]) -> tuple[str, list[tuple[float, str]]]:
    """Render a compact horizontal 2.5D extruded contribution skyline."""
    weeks = weeks[-52:]  # Ensure at most 52 weeks
    all_counts = [
        day["contributionCount"]
        for w in weeks
        for day in w.get("contributionDays", [])
    ]
    max_count = max(all_counts) if all_counts else 1
    max_count = max(max_count, 1)

    # Compact horizontal 2.5D grid geometry
    origin_x = 34.0
    origin_y = 140.0
    col_step = 8.8
    row_dx = 2.4
    row_dy = 17.5
    tile_w = 6.2
    tile_h = 4.2
    max_bar_h = 46.0

    elements: list[str] = []
    month_labels: list[tuple[float, str]] = []
    last_month = ""

    # Draw back-to-front (d: 0..6, w: 0..len-1) for proper 2.5D occlusion
    for w_idx, week in enumerate(weeks):
        first_day = week.get("firstDay", "")
        if first_day:
            month_abbr = datetime.strptime(first_day, "%Y-%m-%d").strftime("%b")
            if month_abbr != last_month and w_idx < len(weeks) - 2:
                # Avoid overlapping labels if too close
                lx = origin_x + w_idx * col_step
                if not month_labels or (lx - month_labels[-1][0]) >= 34:
                    month_labels.append((lx, month_abbr))
                last_month = month_abbr

    for d_idx in range(7):
        for w_idx, week in enumerate(weeks):
            days = week.get("contributionDays", [])
            if d_idx >= len(days):
                continue
            count = days[d_idx]["contributionCount"]
            lvl = level_class(count, max_count)

            bx = origin_x + w_idx * col_step + d_idx * row_dx
            by = origin_y + d_idx * row_dy

            # Subtle sqrt scaling so medium days still stand out cleanly
            if count > 0:
                h = 5.0 + (math.sqrt(count) / math.sqrt(max_count)) * (max_bar_h - 5.0)
            else:
                h = 1.8

            top_y = by - h

            # 2.5D block: top face, front face, right side face
            # Top parallelogram
            p_top = (
                f"{bx:.1f},{top_y:.1f} "
                f"{bx + tile_w:.1f},{top_y:.1f} "
                f"{bx + tile_w + 2.0:.1f},{top_y + tile_h:.1f} "
                f"{bx + 2.0:.1f},{top_y + tile_h:.1f}"
            )
            elements.append(f'<polygon class="lvl-{lvl}-top" points="{p_top}" />')

            if count > 0:
                # Front face
                p_front = (
                    f"{bx + 2.0:.1f},{top_y + tile_h:.1f} "
                    f"{bx + tile_w + 2.0:.1f},{top_y + tile_h:.1f} "
                    f"{bx + tile_w + 2.0:.1f},{by + tile_h:.1f} "
                    f"{bx + 2.0:.1f},{by + tile_h:.1f}"
                )
                # Left/side bevel face
                p_side = (
                    f"{bx:.1f},{top_y:.1f} "
                    f"{bx + 2.0:.1f},{top_y + tile_h:.1f} "
                    f"{bx + 2.0:.1f},{by + tile_h:.1f} "
                    f"{bx:.1f},{by:.1f}"
                )
                elements.append(f'<polygon class="lvl-{lvl}-front" points="{p_front}" />')
                elements.append(f'<polygon class="lvl-{lvl}-side" points="{p_side}" />')

    return "\n    ".join(elements), month_labels


def render_radar(metrics: list[tuple[str, int]]) -> str:
    """Render a compact 5-axis log-scaled activity radar chart."""
    cx, cy = 682.0, 132.0
    radius = 35.0
    n = len(metrics)
    max_log = 3.2  # ~1500 scale

    def point(idx: int, r: float) -> tuple[float, float]:
        angle = -math.pi / 2 + (2 * math.pi * idx) / n
        return cx + r * math.cos(angle), cy + r * math.sin(angle)

    parts: list[str] = []

    # 3 concentric pentagons
    for ring in (0.33, 0.66, 1.0):
        pts = " ".join(f"{point(i, radius * ring)[0]:.1f},{point(i, radius * ring)[1]:.1f}" for i in range(n))
        parts.append(f'<polygon class="radar-grid" points="{pts}" />')

    # Axis lines + labels
    for i, (label, val) in enumerate(metrics):
        ax, ay = point(i, radius)
        parts.append(f'<line class="radar-axis" x1="{cx:.1f}" y1="{cy:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" />')

        lx, ly = point(i, radius + 13.0)
        anchor = "middle"
        if lx < cx - 8:
            anchor = "end"
        elif lx > cx + 8:
            anchor = "start"
        parts.append(
            f'<text class="radar-label" x="{lx:.1f}" y="{ly + 3:.1f}" text-anchor="{anchor}">{label}</text>'
        )

    # Data polygon
    data_pts: list[str] = []
    dot_els: list[str] = []
    for i, (_, val) in enumerate(metrics):
        norm = min(1.0, math.log10(max(val, 0) + 1) / max_log)
        norm = max(norm, 0.12)
        px, py = point(i, radius * norm)
        data_pts.append(f"{px:.1f},{py:.1f}")
        dot_els.append(f'<circle class="radar-dot" cx="{px:.1f}" cy="{py:.1f}" r="2.5" />')

    parts.append(f'<polygon class="radar-area" points="{" ".join(data_pts)}" />')
    parts.extend(dot_els)
    return "\n    ".join(parts)


def render_donut(langs: list[tuple[str, float, str]]) -> str:
    """Render a compact language donut chart + aligned legend."""
    cx, cy = 582.0, 260.0
    r = 25.0
    circ = 2 * math.pi * r
    parts: list[str] = []

    offset = 0.0
    for name, pct, color in langs:
        dash = (pct / 100.0) * circ
        gap = circ - dash
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" '
            f'stroke="{color}" stroke-width="9" '
            f'stroke-dasharray="{dash:.2f} {gap:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" '
            f'transform="rotate(-90 {cx:.1f} {cy:.1f})" />'
        )
        offset += dash

    # Legend on the right of the donut
    lx = 624.0
    ly_start = 234.0
    for idx, (name, pct, color) in enumerate(langs):
        y = ly_start + idx * 14.5
        parts.append(f'<circle cx="{lx:.1f}" cy="{y - 3.5:.1f}" r="3.5" fill="{color}" />')
        parts.append(f'<text class="lang-name" x="{lx + 9:.1f}" y="{y:.1f}">{name}</text>')
        parts.append(f'<text class="lang-pct" x="808" y="{y:.1f}" text-anchor="end">{pct:.1f}%</text>')

    return "\n    ".join(parts)


def build_svg(username: str, user_data: dict) -> str:
    cc = user_data["contributionsCollection"]
    cal = cc["contributionCalendar"]
    total_contrib = cal["totalContributions"]
    commits = cc["totalCommitContributions"]
    prs = cc["totalPullRequestContributions"]
    issues = cc["totalIssueContributions"]
    reviews = cc["totalPullRequestReviewContributions"]
    active_repos = cc["totalRepositoriesWithContributedCommits"]

    skyline_svg, month_labels = render_skyline(cal["weeks"])
    radar_svg = render_radar([
        ("Commits", commits),
        ("Issues", issues),
        ("PRs", prs),
        ("Reviews", reviews),
        ("Repos", active_repos),
    ])
    langs = compute_languages(user_data["repositories"]["nodes"], top_n=4)
    donut_svg = render_donut(langs)

    months_svg = "\n    ".join(
        f'<text class="month-label" x="{x:.1f}" y="294">{label}</text>'
        for x, label in month_labels
    )

    updated = datetime.now(timezone.utc).strftime("%b %d, %Y")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="840" height="320" viewBox="0 0 840 320" role="img" aria-label="GitHub engineering stats for {username}">
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0d1117;
      --panel: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #38bdf8;
      --radar-fill: rgba(56, 189, 248, 0.24);
      --radar-stroke: #38bdf8;
      --grid: #2a313c;
      --l0-t: #1b222c;
      --l1-t: #0e4429; --l1-f: #0a331f; --l1-s: #072617;
      --l2-t: #006d32; --l2-f: #005427; --l2-s: #003e1d;
      --l3-t: #26a641; --l3-f: #1e8734; --l3-s: #166827;
      --l4-t: #39d353; --l4-f: #2eb144; --l4-s: #238c35;
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg: #ffffff;
        --panel: #f6f8fa;
        --border: #d0d7de;
        --text: #1f2328;
        --muted: #656d76;
        --accent: #0969da;
        --radar-fill: rgba(9, 105, 218, 0.18);
        --radar-stroke: #0969da;
        --grid: #d8dee4;
        --l0-t: #eaeef2;
        --l1-t: #9be9a8; --l1-f: #7dd98e; --l1-s: #62c474;
        --l2-t: #40c463; --l2-f: #32a852; --l2-s: #278a41;
        --l3-t: #30a14e; --l3-f: #25853f; --l3-s: #1c6931;
        --l4-t: #216e39; --l4-f: #19562c; --l4-s: #124020;
      }}
    }}
    text {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    .card-bg {{ fill: var(--bg); stroke: var(--border); stroke-width: 1; }}
    .panel {{ fill: var(--panel); stroke: var(--border); stroke-width: 1; }}
    .title {{ fill: var(--text); font-size: 14px; font-weight: 700; }}
    .subtitle {{ fill: var(--muted); font-size: 11px; font-weight: 500; }}
    .kpi-val {{ fill: var(--text); font-size: 14px; font-weight: 700; }}
    .kpi-lbl {{ fill: var(--muted); font-size: 11px; font-weight: 500; }}
    .section-hdr {{ fill: var(--muted); font-size: 10.5px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; }}
    .month-label {{ fill: var(--muted); font-size: 9.5px; font-weight: 500; }}
    .radar-grid {{ fill: none; stroke: var(--grid); stroke-width: 1; }}
    .radar-axis {{ stroke: var(--grid); stroke-width: 1; }}
    .radar-area {{ fill: var(--radar-fill); stroke: var(--radar-stroke); stroke-width: 1.8; stroke-linejoin: round; }}
    .radar-dot {{ fill: var(--radar-stroke); }}
    .radar-label {{ fill: var(--muted); font-size: 10px; font-weight: 600; }}
    .lang-name {{ fill: var(--text); font-size: 11px; font-weight: 500; }}
    .lang-pct {{ fill: var(--muted); font-size: 11px; font-weight: 600; }}
    .lvl-0-top {{ fill: var(--l0-t); }}
    .lvl-1-top {{ fill: var(--l1-t); }} .lvl-1-front {{ fill: var(--l1-f); }} .lvl-1-side {{ fill: var(--l1-s); }}
    .lvl-2-top {{ fill: var(--l2-t); }} .lvl-2-front {{ fill: var(--l2-f); }} .lvl-2-side {{ fill: var(--l2-s); }}
    .lvl-3-top {{ fill: var(--l3-t); }} .lvl-3-front {{ fill: var(--l3-f); }} .lvl-3-side {{ fill: var(--l3-s); }}
    .lvl-4-top {{ fill: var(--l4-t); }} .lvl-4-front {{ fill: var(--l4-f); }} .lvl-4-side {{ fill: var(--l4-s); }}
  </style>

  <!-- Outer Card -->
  <rect class="card-bg" x="0.5" y="0.5" width="839" height="319" rx="12" />

  <!-- Header -->
  <text class="title" x="22" y="30">@{username} · Engineering Activity</text>
  <text class="subtitle" x="22" y="47">Last 12 months · Updated {updated}</text>

  <!-- Top-Right KPIs -->
  <g transform="translate(415, 16)">
    <rect class="panel" x="0" y="0" width="96" height="38" rx="8" />
    <text class="kpi-val" x="48" y="17" text-anchor="middle">{total_contrib:,}</text>
    <text class="kpi-lbl" x="48" y="31" text-anchor="middle">Contributions</text>

    <rect class="panel" x="104" y="0" width="96" height="38" rx="8" />
    <text class="kpi-val" x="152" y="17" text-anchor="middle">{commits:,}</text>
    <text class="kpi-lbl" x="152" y="31" text-anchor="middle">Commits</text>

    <rect class="panel" x="208" y="0" width="96" height="38" rx="8" />
    <text class="kpi-val" x="256" y="17" text-anchor="middle">{prs:,}</text>
    <text class="kpi-lbl" x="256" y="31" text-anchor="middle">Pull Requests</text>

    <rect class="panel" x="312" y="0" width="96" height="38" rx="8" />
    <text class="kpi-val" x="360" y="17" text-anchor="middle">{active_repos:,}</text>
    <text class="kpi-lbl" x="360" y="31" text-anchor="middle">Active Repos</text>
  </g>

  <!-- Left Panel: 2.5D Contribution Skyline -->
  <rect class="panel" x="20" y="66" width="504" height="238" rx="10" />
  <text class="section-hdr" x="34" y="86">CONTRIBUTION SKYLINE (52 WEEKS)</text>
  <g>
    {skyline_svg}
    {months_svg}
  </g>

  <!-- Top-Right Panel: Activity Radar -->
  <rect class="panel" x="536" y="66" width="284" height="120" rx="10" />
  <text class="section-hdr" x="550" y="84">ACTIVITY MIX</text>
  <g>
    {radar_svg}
  </g>

  <!-- Bottom-Right Panel: Top Languages -->
  <rect class="panel" x="536" y="196" width="284" height="108" rx="10" />
  <text class="section-hdr" x="550" y="214">TOP LANGUAGES</text>
  <g>
    {donut_svg}
  </g>
</svg>
"""


def main() -> int:
    username = os.environ.get("GITHUB_USERNAME", "przish")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN environment variable is required.", file=sys.stderr)
        return 1

    out_path = Path(os.environ.get("OUTPUT_PATH", "assets/github-stats.svg"))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    user_data = fetch_github_stats(username, token)
    svg = build_svg(username, user_data)
    out_path.write_text(svg, encoding="utf-8")
    print(f"Generated {out_path} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
