"""Слой работы с SQLite."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init_db() -> None:
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                banned_until  TEXT,
                ban_reason    TEXT,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions(
                chat_id    INTEGER PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                state      TEXT    NOT NULL,
                data       TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_limits(
                user_id INTEGER NOT NULL,
                day     TEXT    NOT NULL,
                count   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, day)
            );

            CREATE TABLE IF NOT EXISTS ads(
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                chat_id       INTEGER NOT NULL,
                ad_text       TEXT    NOT NULL,
                name          TEXT    NOT NULL,
                phone         TEXT    NOT NULL,
                photos        TEXT    NOT NULL,
                channel_id    INTEGER,
                channel_name  TEXT,
                status        TEXT    NOT NULL,
                channel_msg_id INTEGER,
                admin_msg_id  INTEGER,
                reject_reason TEXT,
                created_at    TEXT    NOT NULL,
                published_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS photo_cache(
                url        TEXT PRIMARY KEY,
                token      TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings(
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ads_status ON ads(status);
            CREATE INDEX IF NOT EXISTS idx_ads_user   ON ads(user_id);
            """
        )

# ---------- users ----------
def upsert_user(user_id: int, username: str | None = None) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT INTO users(user_id, username, created_at) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
            """,
            (user_id, username, now_iso()),
        )

def is_banned(user_id: int) -> dict | None:
    with conn() as c:
        r = c.execute(
            "SELECT banned_until, ban_reason FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not r or not r["banned_until"]:
        return None
    try:
        until = datetime.fromisoformat(r["banned_until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if until > datetime.now(timezone.utc):
        return {"banned_until": r["banned_until"], "ban_reason": r["ban_reason"]}
    return None

def ban_user(user_id: int, until_iso: str, reason: str) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT INTO users(user_id, created_at, banned_until, ban_reason)
            VALUES (?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                banned_until=excluded.banned_until,
                ban_reason=excluded.ban_reason
            """,
            (user_id, now_iso(), until_iso, reason),
        )

def unban_user(user_id: int) -> None:
    with conn() as c:
        c.execute(
            "UPDATE users SET banned_until=NULL, ban_reason=NULL WHERE user_id=?",
            (user_id,),
        )

# ---------- sessions ----------
def get_session(chat_id: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(r) if r else None

def set_session(chat_id: int, user_id: int, state: str, data_json: str) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT INTO sessions(chat_id, user_id, state, data, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
                user_id=excluded.user_id,
                state=excluded.state,
                data=excluded.data,
                updated_at=excluded.updated_at
            """,
            (chat_id, user_id, state, data_json, now_iso()),
        )

def clear_session(chat_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE chat_id=?", (chat_id,))

# ---------- daily limits ----------
def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def get_daily_limits(user_id: int, max_ads: int) -> dict:
    with conn() as c:
        r = c.execute(
            "SELECT count FROM daily_limits WHERE user_id=? AND day=?",
            (user_id, _today()),
        ).fetchone()
    used = r["count"] if r else 0
    return {"used": used, "left": max(0, max_ads - used), "max": max_ads}

def increment_daily_limit(user_id: int) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT INTO daily_limits(user_id, day, count) VALUES (?,?,1)
            ON CONFLICT(user_id, day) DO UPDATE SET count=count+1
            """,
            (user_id, _today()),
        )

# ---------- ads ----------
def create_ad(
    user_id: int,
    chat_id: int,
    ad_text: str,
    name: str,
    phone: str,
    photos: list[str],
    channel_id: int,
    channel_name: str,
    status: str = "pending",
) -> int:
    with conn() as c:
        cur = c.execute(
            """
            INSERT INTO ads(
                user_id, chat_id, ad_text, name, phone, photos,
                channel_id, channel_name, status, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id, chat_id, ad_text, name, phone,
                json.dumps(photos, ensure_ascii=False),
                channel_id, channel_name, status, now_iso(),
            ),
        )
        return int(cur.lastrowid)

def get_ad(ad_id: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()
        return dict(r) if r else None

def update_ad_published(ad_id: int, channel_msg_id: int | None, published_at: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE ads SET status='published', channel_msg_id=?, published_at=? WHERE id=?",
            (channel_msg_id, published_at, ad_id),
        )

def update_ad_rejected(ad_id: int, reason_code: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE ads SET status='rejected', reject_reason=? WHERE id=?",
            (reason_code, ad_id),
        )

def update_ad_status(ad_id: int, status: str) -> None:
    with conn() as c:
        c.execute("UPDATE ads SET status=? WHERE id=?", (status, ad_id))

def get_pending_ads() -> list[dict]:
    with conn() as c:
        rs = c.execute("SELECT * FROM ads WHERE status='pending' ORDER BY id").fetchall()
        return [dict(r) for r in rs]

def get_user_ads(user_id: int, limit: int = 10) -> list[dict]:
    with conn() as c:
        rs = c.execute(
            "SELECT * FROM ads WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rs]

def stats() -> dict:
    with conn() as c:
        return {
            "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "ads_total": c.execute("SELECT COUNT(*) FROM ads").fetchone()[0],
            "ads_pending": c.execute("SELECT COUNT(*) FROM ads WHERE status='pending'").fetchone()[0],
            "ads_published": c.execute("SELECT COUNT(*) FROM ads WHERE status='published'").fetchone()[0],
            "ads_rejected": c.execute("SELECT COUNT(*) FROM ads WHERE status='rejected'").fetchone()[0],
            "banned": c.execute(
                "SELECT COUNT(*) FROM users WHERE banned_until > ?", (now_iso(),)
            ).fetchone()[0],
        }

def stats_by_channel() -> list[dict]:
    with conn() as c:
        rs = c.execute(
            """
            SELECT 
                channel_name,
                COUNT(*) as total,
                SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) as published,
                SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected
            FROM ads
            WHERE channel_name IS NOT NULL
            GROUP BY channel_name
            ORDER BY total DESC
            """
        ).fetchall()
        return [dict(r) for r in rs]

# ---------- photo cache ----------
def get_photo_token(url: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT token FROM photo_cache WHERE url=?", (url,)).fetchone()
        return r["token"] if r else None

def save_photo_token(url: str, token: str) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT INTO photo_cache(url, token, created_at) VALUES (?,?,?)
            ON CONFLICT(url) DO UPDATE SET token=excluded.token
            """,
            (url, token, now_iso()),
        )

# ---------- settings ----------
def set_setting(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT INTO settings(key, value) VALUES (?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

def get_setting(key: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None
