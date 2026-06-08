"""
api_server.py — Livetime REST API v2
"""
from __future__ import annotations

import json
import os
import sys
import uuid

# Load API keys from .env
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from pathlib import Path
from typing import Any, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from agent import (
    _fetch_events, _fetch_mood_series, _fetch_skills,
    _get_summary_stats, LivetimeAgent,
)
from seed import get_conn

app = FastAPI(title="Livetime API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_agent: LivetimeAgent | None = None

def get_agent() -> LivetimeAgent:
    global _agent
    if _agent is None:
        _agent = LivetimeAgent()
    return _agent


# ── Read ──────────────────────────────────────────────────────────────────────

@app.get("/api/events")
def list_events(
    year: Optional[int] = None, category: Optional[str] = None,
    momentum: Optional[str] = None, tag: Optional[str] = None,
    limit: int = 50, offset: int = 0,
):
    return _fetch_events(
        year=year, category=category, momentum=momentum,
        tag=tag, limit=min(int(limit), 200), offset=int(offset),
    )


@app.get("/api/events/{event_id}")
def get_event(event_id: str):
    conn = get_conn()
    row = conn.execute(
        """SELECT e.*, et.label AS type_label, et.icon AS type_icon,
                  m.label AS momentum_label, m.color AS momentum_color, m.icon AS momentum_icon
           FROM events e
           LEFT JOIN event_types    et ON et.key = e.type
           LEFT JOIN momentum_types m  ON m.key  = e.momentum
           WHERE e.id = ?""",
        (event_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    ev = dict(row)
    tag_rows = conn.execute(
        "SELECT t.name FROM tags t JOIN event_tags et ON et.tag_id=t.id WHERE et.event_id=?",
        (event_id,),
    ).fetchall()
    ev["tags"] = [r["name"] for r in tag_rows]
    ev["has_media"] = bool(ev["has_media"])
    conn.close()
    return ev


@app.get("/api/search")
def search(q: str, limit: int = 20):
    conn = get_conn()
    like = f"%{q}%"
    rows = conn.execute(
        """SELECT DISTINCT e.id, e.title, e.date_label, e.type, e.momentum, e.description
           FROM events e
           LEFT JOIN event_tags et ON et.event_id=e.id
           LEFT JOIN tags t        ON t.id=et.tag_id
           WHERE e.title LIKE ? OR e.description LIKE ? OR t.name LIKE ?
           ORDER BY e.date_sort DESC LIMIT ?""",
        (like, like, like, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/mood-series")
def mood_series(from_yyyymm: Optional[int] = None, to_yyyymm: Optional[int] = None):
    conn = get_conn()
    conds, params = [], []
    if from_yyyymm:
        conds.append("yyyymm >= ?"); params.append(from_yyyymm)
    if to_yyyymm:
        conds.append("yyyymm <= ?"); params.append(to_yyyymm)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT * FROM monthly_series {where} ORDER BY yyyymm ASC", params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/skills")
def skills():
    return _fetch_skills()


@app.get("/api/okrs")
def okrs(season: Optional[str] = None):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM okrs" + (" WHERE season=?" if season else ""),
        ([season] if season else []),
    ).fetchall()
    result = []
    for r in rows:
        okr = dict(r)
        krs = conn.execute(
            "SELECT title, progress FROM key_results WHERE okr_id=? ORDER BY sort_order",
            (okr["id"],),
        ).fetchall()
        okr["key_results"] = [dict(k) for k in krs]
        result.append(okr)
    conn.close()
    return result


@app.get("/api/stats")
def stats():
    return _get_summary_stats()


@app.get("/api/event-types")
def event_types():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM event_types").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Update ────────────────────────────────────────────────────────────────────

class EventUpdate(BaseModel):
    title: Optional[str] = None
    date_label: Optional[str] = None
    date_sort: Optional[int] = None
    type: Optional[str] = None
    momentum: Optional[str] = None
    description: Optional[str] = None
    link: Optional[str] = None
    tags: Optional[List[str]] = None


@app.put("/api/events/{event_id}")
def update_event(event_id: str, req: EventUpdate):
    conn = get_conn()
    row = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")

    fields, params = [], []
    for attr, col in [
        ("title", "title"), ("date_label", "date_label"),
        ("date_sort", "date_sort"), ("type", "type"),
        ("momentum", "momentum"), ("description", "description"),
        ("link", "link"),
    ]:
        val = getattr(req, attr)
        if val is not None:
            fields.append(f"{col}=?")
            params.append(val)

    if fields:
        fields.append("updated_at=datetime('now')")
        params.append(event_id)
        conn.execute(f"UPDATE events SET {', '.join(fields)} WHERE id=?", params)

    if req.tags is not None:
        conn.execute("DELETE FROM event_tags WHERE event_id=?", (event_id,))
        for tag_name in req.tags:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag_name,))
            tag_id = conn.execute(
                "SELECT id FROM tags WHERE name=?", (tag_name,)
            ).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO event_tags(event_id,tag_id) VALUES(?,?)",
                (event_id, tag_id),
            )

    conn.commit()
    conn.close()
    return get_event(event_id)


# ── Delete ────────────────────────────────────────────────────────────────────

@app.delete("/api/events/{event_id}")
def delete_event(event_id: str):
    conn = get_conn()
    row = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    conn.execute("DELETE FROM event_tags WHERE event_id=?", (event_id,))
    conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()
    conn.close()
    return {"deleted": event_id}


# ── Backup / Import ───────────────────────────────────────────────────────────

@app.get("/api/backup")
def backup_events():
    data = _fetch_events(limit=200)
    return data["events"]


class ImportRequest(BaseModel):
    events: List[Any]


@app.post("/api/import")
def import_events(req: ImportRequest):
    conn = get_conn()
    imported = 0
    for ev in req.events:
        eid = ev.get("id") or f"imp_{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT OR REPLACE INTO events
               (id, title, date_label, date_sort, type, momentum, description, has_media, link)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                eid,
                ev.get("title", "無標題"),
                ev.get("date_label", ""),
                int(ev.get("date_sort", 0)),
                ev.get("type", "learn"),
                ev.get("momentum") or None,
                ev.get("description") or None,
                1 if ev.get("has_media") else 0,
                ev.get("link") or None,
            ),
        )
        conn.execute("DELETE FROM event_tags WHERE event_id=?", (eid,))
        for tag_name in ev.get("tags", []):
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag_name,))
            tag_id = conn.execute(
                "SELECT id FROM tags WHERE name=?", (tag_name,)
            ).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO event_tags(event_id,tag_id) VALUES(?,?)",
                (eid, tag_id),
            )
        imported += 1
    conn.commit()
    conn.close()
    return {"imported": imported}


# ── AI ────────────────────────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    public_only: bool = False

class ChatRequest(BaseModel):
    message: str


def _check_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not set — AI features unavailable",
        )


@app.post("/api/analyze")
def analyze():
    _check_api_key()
    try:
        report = get_agent().chat("/analyze")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI API 錯誤：{str(e)}")
    return {"report": report}


@app.post("/api/export")
def export_events(req: ExportRequest):
    _check_api_key()
    cmd = "/export --public" if req.public_only else "/export"
    return {"output": get_agent().chat(cmd)}


@app.post("/api/chat")
def chat(req: ChatRequest):
    _check_api_key()
    return {"reply": get_agent().chat(req.message)}


if __name__ == "__main__":
    port = 8080
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"Livetime API → http://127.0.0.1:{port}")
    print(f"Docs          → http://127.0.0.1:{port}/docs")
    uvicorn.run("api_server:app", host="127.0.0.1", port=port, reload=True)
