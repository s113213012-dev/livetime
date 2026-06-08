"""
api_server.py — Livetime REST API v3 (with Auth)
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

# Load .env before anything else
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from pathlib import Path

import jwt
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from agent import (
    _fetch_events, _fetch_mood_series, _fetch_skills,
    _get_summary_stats, LivetimeAgent,
)
from seed import get_conn

# ── JWT config ────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("JWT_SECRET", "livetime-change-this-secret-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Livetime API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Per-user agent instances (keeps conversation history per user)
_agents: dict[int, LivetimeAgent] = {}


def get_agent(user_id: int) -> LivetimeAgent:
    if user_id not in _agents:
        _agents[user_id] = LivetimeAgent(user_id=user_id)
    return _agents[user_id]


# ── DB Migration ──────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    conn = get_conn()
    # Users table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            username         TEXT    NOT NULL UNIQUE,
            hashed_password  TEXT    NOT NULL,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Add user_id column to events if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
    if "user_id" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN user_id INTEGER REFERENCES users(id)")
    conn.commit()
    conn.close()


# ── Auth helpers ──────────────────────────────────────────────────────────────
def _create_token(user_id: int, username: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "username": username, "exp": exp},
                      SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="請先登入")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {"user_id": int(payload["sub"]), "username": payload["username"]}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登入已過期，請重新登入")
    except Exception:
        raise HTTPException(status_code=401, detail="無效的登入憑證")


# ── Auth routes ───────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
def register(req: RegisterRequest):
    if len(req.username.strip()) < 2:
        raise HTTPException(400, "使用者名稱至少 2 個字")
    if len(req.password) < 6:
        raise HTTPException(400, "密碼至少 6 個字元")
    conn = get_conn()
    if conn.execute("SELECT id FROM users WHERE username=?", (req.username,)).fetchone():
        conn.close()
        raise HTTPException(400, "此使用者名稱已被使用")
    hashed = pwd_context.hash(req.password)
    cur = conn.execute(
        "INSERT INTO users(username, hashed_password) VALUES(?,?)",
        (req.username.strip(), hashed),
    )
    user_id = cur.lastrowid
    # First user adopts all existing seed events (user_id IS NULL)
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 1:
        conn.execute("UPDATE events SET user_id=? WHERE user_id IS NULL", (user_id,))
    conn.commit()
    conn.close()
    token = _create_token(user_id, req.username.strip())
    return {"token": token, "username": req.username.strip()}


@app.post("/api/auth/login")
def login(req: LoginRequest):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, username, hashed_password FROM users WHERE username=?",
        (req.username,),
    ).fetchone()
    conn.close()
    if not row or not pwd_context.verify(req.password, row["hashed_password"]):
        raise HTTPException(401, "使用者名稱或密碼錯誤")
    token = _create_token(row["id"], row["username"])
    return {"token": token, "username": row["username"]}


# ── Event read ────────────────────────────────────────────────────────────────
@app.get("/api/events")
def list_events(
    year: Optional[int] = None, category: Optional[str] = None,
    momentum: Optional[str] = None, tag: Optional[str] = None,
    limit: int = 50, offset: int = 0,
    user: dict = Depends(get_current_user),
):
    return _fetch_events(
        year=year, category=category, momentum=momentum,
        tag=tag, limit=min(int(limit), 200), offset=int(offset),
        user_id=user["user_id"],
    )


@app.get("/api/events/{event_id}")
def get_event(event_id: str, user: dict = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute(
        """SELECT e.*, et.label AS type_label, et.icon AS type_icon,
                  m.label AS momentum_label, m.color AS momentum_color, m.icon AS momentum_icon
           FROM events e
           LEFT JOIN event_types    et ON et.key = e.type
           LEFT JOIN momentum_types m  ON m.key  = e.momentum
           WHERE e.id = ? AND e.user_id = ?""",
        (event_id, user["user_id"]),
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
def search(q: str, limit: int = 20, user: dict = Depends(get_current_user)):
    conn = get_conn()
    like = f"%{q}%"
    rows = conn.execute(
        """SELECT DISTINCT e.id, e.title, e.date_label, e.type, e.momentum, e.description
           FROM events e
           LEFT JOIN event_tags et ON et.event_id=e.id
           LEFT JOIN tags t        ON t.id=et.tag_id
           WHERE e.user_id=? AND (e.title LIKE ? OR e.description LIKE ? OR t.name LIKE ?)
           ORDER BY e.date_sort DESC LIMIT ?""",
        (user["user_id"], like, like, like, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Event update ──────────────────────────────────────────────────────────────
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
def update_event(event_id: str, req: EventUpdate, user: dict = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM events WHERE id=? AND user_id=?",
        (event_id, user["user_id"]),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="事件不存在或無權限修改")

    fields, params = [], []
    for attr, col in [
        ("title","title"),("date_label","date_label"),("date_sort","date_sort"),
        ("type","type"),("momentum","momentum"),("description","description"),("link","link"),
    ]:
        val = getattr(req, attr)
        if val is not None:
            fields.append(f"{col}=?"); params.append(val)

    if fields:
        fields.append("updated_at=datetime('now')")
        params.append(event_id)
        conn.execute(f"UPDATE events SET {', '.join(fields)} WHERE id=?", params)

    if req.tags is not None:
        conn.execute("DELETE FROM event_tags WHERE event_id=?", (event_id,))
        for tag_name in req.tags:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag_name,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO event_tags(event_id,tag_id) VALUES(?,?)", (event_id, tag_id))

    conn.commit()
    conn.close()
    return get_event(event_id, user)


# ── Event delete ──────────────────────────────────────────────────────────────
@app.delete("/api/events/{event_id}")
def delete_event(event_id: str, user: dict = Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM events WHERE id=? AND user_id=?",
        (event_id, user["user_id"]),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="事件不存在或無權限刪除")
    conn.execute("DELETE FROM event_tags WHERE event_id=?", (event_id,))
    conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()
    conn.close()
    return {"deleted": event_id}


# ── Backup / Import ───────────────────────────────────────────────────────────
@app.get("/api/backup")
def backup_events(user: dict = Depends(get_current_user)):
    data = _fetch_events(limit=200, user_id=user["user_id"])
    return data["events"]


class ImportRequest(BaseModel):
    events: List[Any]


@app.post("/api/import")
def import_events(req: ImportRequest, user: dict = Depends(get_current_user)):
    conn = get_conn()
    imported = 0
    for ev in req.events:
        eid = ev.get("id") or f"imp_{uuid.uuid4().hex[:8]}"
        conn.execute(
            """INSERT OR REPLACE INTO events
               (id, title, date_label, date_sort, type, momentum, description, has_media, link, user_id)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                eid, ev.get("title","無標題"), ev.get("date_label",""),
                int(ev.get("date_sort",0)), ev.get("type","learn"),
                ev.get("momentum") or None, ev.get("description") or None,
                1 if ev.get("has_media") else 0, ev.get("link") or None,
                user["user_id"],
            ),
        )
        conn.execute("DELETE FROM event_tags WHERE event_id=?", (eid,))
        for tag_name in ev.get("tags", []):
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag_name,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO event_tags(event_id,tag_id) VALUES(?,?)", (eid, tag_id))
        imported += 1
    conn.commit()
    conn.close()
    return {"imported": imported}


# ── Stats (public for sidebar connection check) ───────────────────────────────
@app.get("/api/stats")
def stats(user: dict = Depends(get_current_user)):
    return _get_summary_stats(user_id=user["user_id"])


@app.get("/api/mood-series")
def mood_series(user: dict = Depends(get_current_user)):
    return _fetch_mood_series()


@app.get("/api/skills")
def skills(user: dict = Depends(get_current_user)):
    return _fetch_skills()


@app.get("/api/okrs")
def okrs(season: Optional[str] = None, user: dict = Depends(get_current_user)):
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


@app.get("/api/event-types")
def event_types():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM event_types").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Health check (no auth needed) ────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── AI ────────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str

class ExportRequest(BaseModel):
    public_only: bool = False


def _check_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not set — AI features unavailable",
        )


@app.post("/api/analyze")
def analyze(user: dict = Depends(get_current_user)):
    _check_api_key()
    try:
        report = get_agent(user["user_id"]).chat("/analyze")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI API 錯誤：{str(e)}")
    return {"report": report}


@app.post("/api/export")
def export_events(req: ExportRequest, user: dict = Depends(get_current_user)):
    _check_api_key()
    cmd = "/export --public" if req.public_only else "/export"
    return {"output": get_agent(user["user_id"]).chat(cmd)}


@app.post("/api/chat")
def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    _check_api_key()
    return {"reply": get_agent(user["user_id"]).chat(req.message)}


if __name__ == "__main__":
    port = 8080
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"Livetime API → http://127.0.0.1:{port}")
    print(f"Docs          → http://127.0.0.1:{port}/docs")
    uvicorn.run("api_server:app", host="127.0.0.1", port=port, reload=True)
