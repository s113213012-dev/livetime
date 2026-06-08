"""
agent.py — Livetime AI Agent
Slash-command parser + Anthropic API integration.

    python agent.py                        # interactive REPL
    python agent.py "/timeline 2025 work"  # single command
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import os
import google.generativeai as genai

from seed import get_conn

SYSTEM_PROMPT = (Path(__file__).parent / "agent_system_prompt.md").read_text(
    encoding="utf-8"
)

CATEGORY_ALIASES: dict[str, str] = {
    "學習": "learn", "learn": "learn",
    "作品": "work",  "work":  "work",
    "實習": "intern","intern":"intern",
    "工作": "job",   "job":   "job",
    "生活": "life",  "life":  "life",
}

MOMENTUM_EMOJI = {"up": "⬆️", "calm": "🌊", "intense": "⚡"}


def _fetch_events(
    year=None, category=None, momentum=None, tag=None, limit=100, offset=0,
) -> dict[str, Any]:
    conn = get_conn()
    conds, params = [], []
    if year:
        conds.append("e.date_sort / 100 = ?"); params.append(year)
    if category:
        conds.append("e.type = ?"); params.append(category)
    if momentum:
        conds.append("e.momentum = ?"); params.append(momentum)
    if tag:
        conds.append(
            "e.id IN (SELECT et.event_id FROM event_tags et "
            "JOIN tags t ON t.id=et.tag_id WHERE t.name LIKE ?)"
        )
        params.append(f"%{tag}%")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = conn.execute(f"SELECT COUNT(*) FROM events e {where}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT e.id, e.title, e.date_label, e.date_sort, e.year, e.month,
                   e.type, et.label AS type_label,
                   e.momentum, m.label AS momentum_label, m.color AS momentum_color,
                   e.description, e.has_media, e.link
            FROM events e
            LEFT JOIN event_types    et ON et.key=e.type
            LEFT JOIN momentum_types m  ON m.key=e.momentum
            {where} ORDER BY e.date_sort DESC LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    events = [dict(r) for r in rows]
    for ev in events:
        tag_rows = conn.execute(
            "SELECT t.name FROM tags t JOIN event_tags et ON et.tag_id=t.id WHERE et.event_id=?",
            (ev["id"],),
        ).fetchall()
        ev["tags"] = [r["name"] for r in tag_rows]
        ev["has_media"] = bool(ev["has_media"])
    conn.close()
    return {"events": events, "total": total}


def _fetch_mood_series():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM monthly_series ORDER BY yyyymm ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fetch_skills():
    conn = get_conn()
    rows = conn.execute("SELECT name, value FROM skills ORDER BY value DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_summary_stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    by_type = [dict(r) for r in conn.execute(
        "SELECT e.type, et.label, COUNT(*) AS count FROM events e "
        "JOIN event_types et ON et.key=e.type GROUP BY e.type ORDER BY count DESC"
    ).fetchall()]
    date_range = dict(conn.execute(
        "SELECT MIN(date_sort) AS earliest, MAX(date_sort) AS latest FROM events"
    ).fetchone())
    all_tags = [r["name"] for r in conn.execute(
        "SELECT t.name, COUNT(et.event_id) n FROM tags t "
        "JOIN event_tags et ON et.tag_id=t.id GROUP BY t.id ORDER BY n DESC"
    ).fetchall()]
    conn.close()
    return {"total_events": total, "by_category": by_type,
            "date_range": date_range, "all_tags": all_tags}


def render_timeline(year, category):
    result = _fetch_events(year=year, category=category)
    events = result["events"]
    total = result["total"]

    if not events:
        parts = []
        if year:     parts.append(f"{year} 年")
        if category: parts.append(f"分類：{category}")
        desc = f"（{'·'.join(parts)}）" if parts else ""
        return f"> 找不到符合條件的事件{desc}。"

    lines: list[str] = []
    for ev in events:
        emoji = MOMENTUM_EMOJI.get(ev.get("momentum", ""), "")
        tags_str = " ".join(f"`{t}`" for t in ev.get("tags", []))
        link_line = f"🔗 [{ev['link']}]({ev['link']})" if ev.get("link") else ""
        block = [
            "---",
            f"### {ev['date_label']} · {ev.get('type_label', ev['type'])}",
            "",
            f"**{ev['title']}**",
            "",
            ev.get("description") or "",
            "",
            f"**情緒狀態**：{emoji} {ev.get('momentum_label', '')}",
            f"**技能標籤**：{tags_str}",
        ]
        if link_line:
            block.append(link_line)
        lines.extend(block)
        lines.append("")

    parts = []
    if year:     parts.append(f"{year} 年")
    if category: parts.append(f"分類：{category}")
    desc = f"（{'·'.join(parts)}）" if parts else ""
    lines.append(f"> 共 {total} 筆事件{desc}")
    return "\n".join(lines)


def build_analyze_context():
    ctx = {
        "summary_stats": _get_summary_stats(),
        "events": [
            {k: v for k, v in e.items()
             if k in ("id","title","date_label","date_sort","type","type_label",
                      "momentum","momentum_label","description","tags")}
            for e in _fetch_events(limit=100)["events"]
        ],
        "mood_series": _fetch_mood_series(),
        "skills": _fetch_skills(),
    }
    return json.dumps(ctx, ensure_ascii=False, indent=2)


def build_export_context(public_only):
    events = _fetch_events(limit=100)["events"]
    if public_only:
        events = [e for e in events if e["type"] != "life"]
    counts: dict[str, int] = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    return events, {"total": len(events), "categories": counts}


def parse_slash(text):
    text = text.strip()
    if not text.startswith("/"):
        return "chat", {"text": text}
    parts = text.split()
    cmd = parts[0].lstrip("/").lower()
    if cmd == "timeline":
        year, category = None, None
        for token in parts[1:]:
            if re.fullmatch(r"\d{4}", token):
                year = int(token)
            elif token.lower() in CATEGORY_ALIASES:
                category = CATEGORY_ALIASES[token.lower()]
        return "timeline", {"year": year, "category": category}
    if cmd == "analyze":
        return "analyze", {}
    if cmd == "export":
        return "export", {"public_only": "--public" in parts}
    return "unknown", {"original": text}


UNKNOWN_HELP = """目前支援的指令：
• `/timeline [年份] [分類]` — 瀏覽時間軸
• `/analyze` — 深度洞察報告
• `/export [--public]` — 匯出作品集 JSON

輸入指令或直接用自然語言問我！"""


class LivetimeAgent:
    def __init__(self, model: str = "gemini-2.0-flash"):
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
        self.model = genai.GenerativeModel(
            model_name=model,
            system_instruction=SYSTEM_PROMPT,
        )
        self.history: list[dict] = []

    def chat(self, user_input: str) -> str:
        cmd, kwargs = parse_slash(user_input)
        if cmd == "timeline":
            return render_timeline(kwargs["year"], kwargs["category"])
        if cmd == "unknown":
            return UNKNOWN_HELP
        if cmd == "analyze":
            context = build_analyze_context()
            injected = (
                "使用者輸入了 `/analyze`。\n\n"
                f"資料庫資料（JSON）：\n\n```json\n{context}\n```\n\n"
                "請依照 System Prompt 的 `/analyze` 格式生成完整洞察報告。"
            )
            return self._ask_gemini(injected, stateful=False)
        if cmd == "export":
            events, meta = build_export_context(kwargs["public_only"])
            events_json = json.dumps(events, ensure_ascii=False, indent=2)
            flag = " --public" if kwargs["public_only"] else ""
            injected = (
                f"使用者輸入了 `/export{flag}`。\n\n"
                f"事件資料：\n\n```json\n{events_json}\n```\n\n"
                f"meta：{json.dumps(meta, ensure_ascii=False)}\n"
                f"exported_at: {date.today().isoformat()}\n\n"
                "請依照 System Prompt 的 `/export` 格式潤飾並輸出 JSON。"
            )
            return self._ask_gemini(injected, stateful=False)
        if cmd == "chat":
            text = kwargs["text"]
            data_kw = re.compile(
                r"事件|技能|心情|情緒|幾筆|做了什麼|分析|OKR|作品|實習|學習|工作|生活|Figma|Python|React",
                re.IGNORECASE,
            )
            if data_kw.search(text):
                stats = _get_summary_stats()
                text += f"\n\n[工具上下文] 統計摘要：{json.dumps(stats, ensure_ascii=False)}"
            return self._ask_gemini(text, stateful=True)
        return UNKNOWN_HELP

    def _ask_gemini(self, user_content: str, stateful: bool) -> str:
        chat = self.model.start_chat(history=self.history)
        response = chat.send_message(user_content)
        reply = response.text
        if stateful:
            self.history.append({"role": "user", "parts": [user_content]})
            self.history.append({"role": "model", "parts": [reply]})
            self.history = self.history[-40:]
        return reply


def main():
    agent = LivetimeAgent()
    if len(sys.argv) > 1:
        print(agent.chat(" ".join(sys.argv[1:])))
        return
    print("時光機 AI 助理已啟動。輸入 /help 查看指令，Ctrl+C 離開。\n")
    while True:
        try:
            user_input = input("你：").strip()
            if not user_input:
                continue
            print(f"\n助理：\n{agent.chat(user_input)}\n")
        except KeyboardInterrupt:
            print("\n掰掰！"); break


if __name__ == "__main__":
    main()
