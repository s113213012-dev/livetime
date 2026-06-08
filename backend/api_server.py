"""
api_server.py — Livetime REST API
===================================
Thin HTTP wrapper around the SQLite helpers.

    python api_server.py            # default :8080
    python api_server.py --port 3001

CORS is open so GitHub Pages (or any origin) can reach a local/ngrok server.
"""
from __future__ import annotations

import os
import sys

# Load ANTHROPIC_API_KEY from .env if not already set in the environment
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file) and not os.environ.get("ANTHROPIC_API_KEY"):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
from pathlib import Path
from typing import Optional

import anthropic
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

app = FastAPI(title="Livetime API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_agent: LivetimeAgent | None = None

def get_agent() -> LivetimeAgent:
    global _agent
    if _agent is None:
        _agent = LivetimeAgent()
    return _agent


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
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API 錯誤：{e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="無法連線至 Anthropic API，請確認網路或 API Key 是否正確")
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
