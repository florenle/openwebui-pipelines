# lfb_sqlite.py
# Role: SQLite-based storage for lfbrain pipeline. Single source of truth for all chat data.
#       Replaces context.json and last.json.
#
# Key Functions:
#   init_db(): Creates lfbrain.db and all tables if not exist. Call once at pipeline startup.
#   get_conn(): Returns a WAL-enabled sqlite3 connection with row_factory set.
#   --- chats ---
#   create_chat(chat_id, title): Creates a new chat row.
#   get_chat(chat_id): Returns chat row or None.
#   update_chat_title(chat_id, title): Updates title and last_updated.
#   update_chat_summary(chat_id, summary): Updates summary and last_updated.
#   toggle_save(chat_id): Flips save flag 0↔1.
#   list_chats(): Returns all chats as {chat_id, title, summary}.
#   delete_chat(chat_id): Deletes chat and cascades to blocks, jobs, docs.
#
# Dependencies:
#   None (stdlib only: sqlite3, datetime, uuid)
#
# Dev Notes:
#   DB lives at /home/florenle/x/dev/openwebui/chats/lfbrain.db
#   WAL mode enabled for safe concurrent access
#   Foreign keys enforced — deleting a chat cascades to blocks, jobs, docs
#   Block seq is auto-assigned per chat — used for display ordering and /rmb targeting
#   Slash command turns are never written to the DB

import sqlite3
from datetime import datetime, timezone

DB_PATH = "/home/florenle/x/dev/openwebui/chats/lfbrain.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id      TEXT PRIMARY KEY,
                title        TEXT,
                summary      TEXT,
                created_at   TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                save         INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS blocks (
                block_id           TEXT PRIMARY KEY,
                chat_id            TEXT NOT NULL,
                seq                INTEGER NOT NULL,
                user_content       TEXT,
                system_content     TEXT,
                assistant_content  TEXT,
                created_at         TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
                UNIQUE (chat_id, seq)
            );
            CREATE TABLE IF NOT EXISTS jobs (
                job_id      TEXT PRIMARY KEY,
                block_id    TEXT NOT NULL,
                chat_id     TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'running',
                killme      INTEGER NOT NULL DEFAULT 0,
                error       TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (block_id) REFERENCES blocks(block_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS docs (
                doc_id      TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL,
                filename    TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
            );
        """)
        # LFB02242026B: clean up orphaned jobs from previous crash
        conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'orphaned at startup' WHERE status = 'running'"
        )        
    conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_chat(chat_id, title=None):
    conn = get_conn()
    now = _now()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO chats (chat_id, title, created_at, last_updated) VALUES (?, ?, ?, ?)",
            (chat_id, title, now, now)
        )
    conn.close()


def get_chat(chat_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_chat_title(chat_id, title):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET title = ?, last_updated = ? WHERE chat_id = ?",
            (title, _now(), chat_id)
        )
    conn.close()


def update_chat_summary(chat_id, summary):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET summary = ?, last_updated = ? WHERE chat_id = ?",
            (summary, _now(), chat_id)
        )
    conn.close()


def toggle_save(chat_id):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET save = 1 - save, last_updated = ? WHERE chat_id = ?",
            (_now(), chat_id)
        )
    conn.close()


def list_chats():
    conn = get_conn()
    rows = conn.execute("SELECT chat_id, title, summary FROM chats ORDER BY last_updated DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_chat(chat_id):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
    conn.close()
