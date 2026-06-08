"""seed.py — populate the database with the sample data from the HTML prototype."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "livetime.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def seed():
    conn = get_conn()
    conn.executescript(SCHEMA_PATH.read_text())

    conn.executemany(
        "INSERT OR REPLACE INTO event_types(key, label, icon, color) VALUES(?,?,?,?)",
        [
            ("learn",  "學習", "learn",  "var(--t-learn)"),
            ("work",   "作品", "work",   "var(--t-work)"),
            ("intern", "實習", "intern", "var(--t-intern)"),
            ("job",    "工作", "job",    "var(--t-job)"),
            ("life",   "生活", "life",   "var(--t-life)"),
        ],
    )

    conn.executemany(
        "INSERT OR REPLACE INTO momentum_types(key, label, icon, color, soft) VALUES(?,?,?,?,?)",
        [
            ("up",      "成就感高", "arrowUp", "#34e3a8", "rgba(52,227,168,.16)"),
            ("calm",    "沉澱期",   "wave",    "#5ad1ff", "rgba(90,209,255,.16)"),
            ("intense", "高壓衝刺", "bolt",    "#ffc861", "rgba(255,200,97,.16)"),
        ],
    )

    events = [
        ("e1",  "Livetime 個人時光軸上線",      "2026 · 3月",        202603, "work",   "up",
         "把四年的學習、作品與生活收進一條互動時間軸，串接 AI 自動標籤與情緒分析。設計、前端到部署一手包辦。",
         1, "livetime.app"),
        ("e2",  "HackTime 黑客松 — 最佳設計獎", "2026 · 1月",        202601, "work",   "intense",
         "36 小時內帶領三人小組做出一款記帳語音助理，負責產品流程與全部視覺。第一次上台 Demo。",
         1, None),
        ("e3",  "星辰科技 — UI/UX 設計實習",    "2025 · 9月 – 12月", 202509, "intern", "intense",
         "在真實產品團隊裡畫了 20+ 份 Wireframe、整理一套設計系統元件庫。主管很願意帶人，但專案後期密集加班。",
         0, None),
        ("e4",  "完成 Google UX Design 認證",    "2025 · 6月",        202506, "learn",  "up",
         "七門課、一份完整作品集專案。最大的收穫是學會用使用者研究替設計決策說話。",
         0, "coursera.org"),
        ("e5",  "自學 Python 與資料視覺化",      "2025 · 3月",        202503, "learn",  "calm",
         "寒假慢慢啃 pandas 與 matplotlib，替系上活動做了一份報名數據儀表板。步調很慢但很踏實。",
         1, None),
        ("e6",  "校園導覽 App — 大三專題",       "2024 · 11月",       202411, "work",   "up",
         "帶四人團隊從訪談、原型到使用者測試，做出一款新生校園導覽 App，期末拿到全班最高分。",
         1, None),
        ("e7",  "暑期打工 + 一個人的東部旅行",   "2024 · 7月",        202407, "life",   "calm",
         "在咖啡廳打工兩個月，存下旅費，獨自搭火車環島東半部。把生活按下慢速鍵，反而想清楚想做的方向。",
         1, None),
        ("e8",  "設計社 — 接任視覺組長",         "2024 · 2月",        202402, "job",    "up",
         "統籌社團一整年的視覺識別與活動主視覺，第一次管理一個六人的設計小組。學會把品味變成可溝通的規則。",
         0, None),
        ("e9",  "升大三 · 主修使用者經驗",       "2023 · 9月",        202309, "learn",  "calm",
         "正式選定 HCI 與互動設計方向，開始大量閱讀設計理論，也常常懷疑自己到底適不適合。",
         0, None),
        ("e10", "第一個 Figma 作品",             "2023 · 5月",        202305, "learn",  "up",
         "照著 YouTube 教學重做了一遍音樂 App 介面，第一次感受到把腦中畫面變成像素的快樂。一切的起點。",
         1, None),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO events"
        "(id, title, date_label, date_sort, type, momentum, description, has_media, link)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        events,
    )

    raw_tags = {
        "e1":  ["UI/UX", "React", "資料視覺化", "Side Project"],
        "e2":  ["Product Design", "Figma", "Pitch"],
        "e3":  ["Figma", "Wireframing", "Design System", "協作"],
        "e4":  ["UX Research", "Prototyping", "可用性測試"],
        "e5":  ["Python", "資料分析", "Matplotlib"],
        "e6":  ["專案管理", "UI", "使用者研究"],
        "e7":  ["充電", "攝影", "生活"],
        "e8":  ["Leadership", "品牌設計", "Branding"],
        "e9":  ["HCI", "互動設計"],
        "e10": ["Figma", "UI"],
    }
    for event_id, tag_names in raw_tags.items():
        for name in tag_names:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO event_tags(event_id, tag_id) VALUES(?,?)",
                (event_id, row["id"]),
            )

    series = [
        ("23/05", 202305, 78, 42), ("23/09", 202309, 54, 50),
        ("24/02", 202402, 80, 68), ("24/07", 202407, 62, 30),
        ("24/11", 202411, 88, 82), ("25/03", 202503, 58, 55),
        ("25/06", 202506, 84, 74), ("25/09", 202509, 60, 92),
        ("25/12", 202512, 48, 88), ("26/01", 202601, 72, 95),
        ("26/03", 202603, 90, 80),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO monthly_series(month, yyyymm, mood, prod) VALUES(?,?,?,?)",
        series,
    )

    okrs = [
        ("o1", "2026 上半年", "成為能獨當一面的產品設計師", "var(--t-work)"),
        ("o2", "2026 上半年", "補強工程與資料能力",         "var(--t-learn)"),
        ("o3", "長期",        "維持身心的續航力",           "var(--t-life)"),
    ]
    conn.executemany("INSERT OR REPLACE INTO okrs(id, season, obj, color) VALUES(?,?,?,?)", okrs)

    krs = [
        ("o1", "產出一份完整的個人作品集網站", 100, 0),
        ("o1", "主導一個 0→1 的產品專案",      65,  1),
        ("o1", "累積 3 場公開設計分享",         33,  2),
        ("o2", "用 React 獨立完成 2 個專案",    80,  0),
        ("o2", "學會用資料佐證設計決策",         50,  1),
        ("o2", "讀完《Design for Real Life》",  20,  2),
        ("o3", "每週運動 3 次",                 45,  0),
        ("o3", "每季安排一次完整休假",           75,  1),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO key_results(okr_id, title, progress, sort_order) VALUES(?,?,?,?)",
        krs,
    )

    skills = [
        ("UI 設計",   88), ("使用者研究", 72), ("前端開發",   64),
        ("專案管理",   70), ("資料分析",   48), ("品牌視覺",   78),
    ]
    conn.executemany("INSERT OR REPLACE INTO skills(name, value) VALUES(?,?)", skills)

    conn.commit()
    conn.close()
    print(f"✓ Database seeded at {DB_PATH}")


if __name__ == "__main__":
    seed()
