# lfb_sqlite.py
# Status: In Development
# Role: SQLite-based storage for lfbrain pipeline. Single source of truth for all chat data.
#
# Key Functions:
#   init_db(): Creates lfbrain.db and all tables if not exist. Call once at pipeline startup.
#   get_conn(): Returns a WAL-enabled sqlite3 connection with row_factory set.
#   --- chats ---
#   create_chat(chat_id, title): Creates a new chat row.
#   get_chat(chat_id): Returns chat row or None.
#   update_chat_title(chat_id, title): Updates title and last_updated.
#   update_chat_description(chat_id, description): Updates description and last_updated.
#   update_chat_summary(chat_id, summary): Updates summary and last_updated.
#   update_chat_model_hint(chat_id, model_hint): Updates model_hint and last_updated.
#   clear_chat_summaries(chat_id): Sets description=NULL, summary=NULL, updates last_updated.
#   toggle_save(chat_id): Flips save flag 0↔1.
#   list_chats(): Returns all chats as {chat_id, title, description}.
#   delete_chat(chat_id): Deletes chat and cascades to blocks, jobs, docs.
#
# Dependencies:
#   lfb_log: log()
#
# Dev Notes:
#   DB lives at /home/florenle/x/dev/openwebui/chats/lfbrain.db
#   WAL mode enabled for safe concurrent access
#   Foreign keys enforced — deleting a chat cascades to blocks, jobs, docs
#   Slash command turns are never written to the DB
#
# Schema: LFB03112026A

import sqlite3
from datetime import datetime, timezone
from lfb_log import log

DB_PATH = "/home/florenle/x/dev/openwebui/chats/lfbrain.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    log("lfb_sqlite", "init_db() called")
    conn = get_conn()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id      TEXT PRIMARY KEY,
                title        TEXT,
                description  TEXT,
                summary      TEXT,
                created_at   TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                save         INTEGER NOT NULL DEFAULT 0,
                model_hint   TEXT NOT NULL DEFAULT 'local'
            );
            CREATE TABLE IF NOT EXISTS blocks (
                block_id          TEXT PRIMARY KEY,
                chat_id           TEXT NOT NULL,
                seq               INTEGER NOT NULL,
                owui_message_id   TEXT,
                user_content      TEXT,
                assistant_content TEXT,
                incomplete        INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT NOT NULL,
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
        conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'orphaned at startup' WHERE status = 'running'"
        )
    conn.close()
    log("lfb_sqlite", "init_db() complete")


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_chat(chat_id, title=None):
    log("lfb_sqlite", f"create_chat({chat_id})")
    conn = get_conn()
    now = _now()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO chats (chat_id, title, created_at, last_updated) VALUES (?, ?, ?, ?)",
            (chat_id, title, now, now)
        )
    conn.close()


def get_chat(chat_id):
    log("lfb_sqlite", f"get_chat({chat_id})")
    conn = get_conn()
    row = conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    result = dict(row) if row else None
    log("lfb_sqlite", f"get_chat({chat_id}) → {result}")
    return result


def update_chat_title(chat_id, title):
    log("lfb_sqlite", f"update_chat_title({chat_id}, {title})")
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET title = ?, last_updated = ? WHERE chat_id = ?",
            (title, _now(), chat_id)
        )
    conn.close()


def update_chat_description(chat_id, description):
    log("lfb_sqlite", f"update_chat_description({chat_id}, {description})")
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET description = ?, last_updated = ? WHERE chat_id = ?",
            (description, _now(), chat_id)
        )
    conn.close()


def update_chat_summary(chat_id, summary):
    log("lfb_sqlite", f"update_chat_summary({chat_id}, {summary})")
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET summary = ?, last_updated = ? WHERE chat_id = ?",
            (summary, _now(), chat_id)
        )
    conn.close()

def update_chat_model_hint(chat_id, model_hint):
    log("lfb_sqlite", f"update_chat_model_hint({chat_id}, {model_hint})")
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET model_hint = ?, last_updated = ? WHERE chat_id = ?",
            (model_hint, _now(), chat_id)
        )
    conn.close()

def clear_chat_summaries(chat_id):
    log("lfb_sqlite", f"clear_chat_summaries({chat_id})")
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET description = NULL, summary = NULL, last_updated = ? WHERE chat_id = ?",
            (_now(), chat_id)
        )
    conn.close()


def toggle_save(chat_id):
    log("lfb_sqlite", f"toggle_save({chat_id})")
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE chats SET save = 1 - save, last_updated = ? WHERE chat_id = ?",
            (_now(), chat_id)
        )
    conn.close()


def list_chats():
    log("lfb_sqlite", "list_chats()")
    conn = get_conn()
    rows = conn.execute(
        "SELECT chat_id, title, description, last_updated FROM chats ORDER BY last_updated DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_chat(chat_id):
    log("lfb_sqlite", f"delete_chat({chat_id})")
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
    conn.close()
