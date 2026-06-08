"""
mcp_server.py — Livetime MCP Server
Exposes the timeline database to AI agents via the Model Context Protocol.

    python mcp_server.py           # SSE on http://127.0.0.1:8000/sse
    python mcp_server.py --stdio   # stdio (for Claude Desktop)
"""
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

DB_PATH = Path(__file__).parent / "livetime.db"

mcp = FastMCP(
    name="livetime",
    instructions=(
        "You are connected to the Livetime 時光機 personal timeline database. "
        "Use the tools to query life events, mood series, OKRs, and skills. "
        "All content is in Traditional Chinese."
    ),
)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _rows(rows): return [dict(r) for r in rows]


@mcp.tool()
def fetch_timeline_events(
    year: Optional[int] = None, category: Optional[str] = None,
    momentum: Optional[str] = None, tag: Optional[str] = None,
    limit: int = 50, offset: int = 0,
) -> dict:
    """Retrieve timeline events. Supports year, category, momentum, tag filters."""
    conn = _conn()
    conds, params = [], []
    if year:     conds.append("e.date_sort/100=?"); params.append(year)
    if category: conds.append("e.type=?");          params.append(category)
    if momentum: conds.append("e.momentum=?");      params.append(momentum)
    if tag:
        conds.append("e.id IN (SELECT et.event_id FROM event_tags et "
                     "JOIN tags t ON t.id=et.tag_id WHERE t.name LIKE ?)")
        params.append(f"%{tag}%")
    where = ("WHERE "+" AND ".join(conds)) if conds else ""
    total = conn.execute(f"SELECT COUNT(*) FROM events e {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT e.id,e.title,e.date_label,e.date_sort,e.year,e.month,
                   e.type,et.label AS type_label,
                   e.momentum,m.label AS momentum_label,
                   e.description,e.has_media,e.link
            FROM events e
            LEFT JOIN event_types et ON et.key=e.type
            LEFT JOIN momentum_types m ON m.key=e.momentum
            {where} ORDER BY e.date_sort DESC LIMIT ? OFFSET ?""",
        params+[limit, offset],
    ).fetchall()
    events = _rows(rows)
    for ev in events:
        tr = conn.execute(
            "SELECT t.name FROM tags t JOIN event_tags et ON et.tag_id=t.id WHERE et.event_id=?",
            (ev["id"],),
        ).fetchall()
        ev["tags"] = [r["name"] for r in tr]
        ev["has_media"] = bool(ev["has_media"])
    conn.close()
    return {"events": events, "total": total}


@mcp.tool()
def get_event_detail(event_id: str) -> dict:
    """Get full details for a single event by ID."""
    conn = _conn()
    row = conn.execute(
        "SELECT e.*,et.label AS type_label,m.label AS momentum_label "
        "FROM events e LEFT JOIN event_types et ON et.key=e.type "
        "LEFT JOIN momentum_types m ON m.key=e.momentum WHERE e.id=?",
        (event_id,),
    ).fetchone()
    if row is None: raise ValueError(f"Event '{event_id}' not found")
    ev = dict(row)
    ev["tags"] = [r["name"] for r in conn.execute(
        "SELECT t.name FROM tags t JOIN event_tags et ON et.tag_id=t.id WHERE et.event_id=?",
        (event_id,),
    ).fetchall()]
    conn.close()
    return ev


@mcp.tool()
def fetch_mood_series(from_yyyymm: Optional[int]=None, to_yyyymm: Optional[int]=None) -> list:
    """Monthly mood & productivity series."""
    conn = _conn()
    conds, params = [], []
    if from_yyyymm: conds.append("yyyymm>=?"); params.append(from_yyyymm)
    if to_yyyymm:   conds.append("yyyymm<=?"); params.append(to_yyyymm)
    where = ("WHERE "+" AND ".join(conds)) if conds else ""
    rows = conn.execute(f"SELECT * FROM monthly_series {where} ORDER BY yyyymm", params).fetchall()
    conn.close()
    return _rows(rows)


@mcp.tool()
def fetch_skills() -> list:
    """Skill radar values (0-100)."""
    conn = _conn()
    rows = conn.execute("SELECT name,value FROM skills ORDER BY value DESC").fetchall()
    conn.close()
    return _rows(rows)


@mcp.tool()
def fetch_okrs(season: Optional[str]=None) -> list:
    """OKR board with key results."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM okrs"+(" WHERE season=?" if season else ""),
        ([season] if season else []),
    ).fetchall()
    result = []
    for r in rows:
        okr = dict(r)
        okr["key_results"] = _rows(conn.execute(
            "SELECT title,progress FROM key_results WHERE okr_id=? ORDER BY sort_order",
            (okr["id"],),
        ).fetchall())
        result.append(okr)
    conn.close()
    return result


@mcp.tool()
def search_events(query: str, limit: int=20) -> list:
    """Full-text search across titles, descriptions, and tags."""
    conn = _conn()
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT DISTINCT e.id,e.title,e.date_label,e.type,e.momentum,e.description "
        "FROM events e LEFT JOIN event_tags et ON et.event_id=e.id "
        "LEFT JOIN tags t ON t.id=et.tag_id "
        "WHERE e.title LIKE ? OR e.description LIKE ? OR t.name LIKE ? "
        "ORDER BY e.date_sort DESC LIMIT ?",
        (like, like, like, limit),
    ).fetchall()
    conn.close()
    return _rows(rows)


@mcp.tool()
def get_summary_stats() -> dict:
    """Aggregate statistics: counts, category breakdown, date range, all tags."""
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    by_type = _rows(conn.execute(
        "SELECT e.type,et.label,COUNT(*) AS count FROM events e "
        "JOIN event_types et ON et.key=e.type GROUP BY e.type ORDER BY count DESC"
    ).fetchall())
    dr = dict(conn.execute(
        "SELECT MIN(date_sort) AS earliest,MAX(date_sort) AS latest FROM events"
    ).fetchone())
    tags = [r["name"] for r in conn.execute(
        "SELECT t.name,COUNT(et.event_id) n FROM tags t "
        "JOIN event_tags et ON et.tag_id=t.id GROUP BY t.id ORDER BY n DESC"
    ).fetchall()]
    conn.close()
    return {"total_events": total, "by_category": by_type, "date_range": dr, "all_tags": tags}


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host="127.0.0.1", port=8000)
