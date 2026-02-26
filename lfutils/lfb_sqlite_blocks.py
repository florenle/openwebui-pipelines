# lfb_sqlite_blocks.py
# Role: CRUD operations for the blocks table.
#       Each block represents one user turn: user + system status + assistant result.
#
# Key Functions:
#   add_block(chat_id, block_id, user_content): Inserts new block, auto-assigns next seq.
#   get_block(block_id): Returns block row or None.
#   get_blocks_by_chat(chat_id): Returns all blocks for a chat ordered by seq.
#   update_block_system(block_id, system_content): Stores job status stream.
#   update_block_assistant(block_id, assistant_content): Stores final LLM result.
#   delete_blocks_by_seq_range(chat_id, seqs): Deletes blocks by list of seq numbers.
#
# Dependencies:
#   lfb_sqlite: get_conn()
#
# Dev Notes:
#   seq is auto-assigned as max(seq)+1 per chat at insert time
#   block_id is a UUID generated in pipe() at job submission time
#   Slash command turns are never stored — no block is created for them

import sqlite3
from datetime import datetime, timezone
from lfb_sqlite import get_conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_block(chat_id, block_id, user_content):
    conn = get_conn()
    with conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM blocks WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        next_seq = row["next_seq"]
        conn.execute(
            """INSERT INTO blocks (block_id, chat_id, seq, user_content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (block_id, chat_id, next_seq, user_content, _now())
        )
    conn.close()
    return next_seq


def get_block(block_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM blocks WHERE block_id = ?", (block_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_blocks_by_chat(chat_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM blocks WHERE chat_id = ? ORDER BY seq", (chat_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_block_system(block_id, system_content):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE blocks SET system_content = ? WHERE block_id = ?",
            (system_content, block_id)
        )
    conn.close()


def update_block_assistant(block_id, assistant_content):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE blocks SET assistant_content = ? WHERE block_id = ?",
            (assistant_content, block_id)
        )
    conn.close()


def delete_blocks_by_seq_range(chat_id, seqs):
    conn = get_conn()
    with conn:
        conn.execute(
            f"DELETE FROM blocks WHERE chat_id = ? AND seq IN ({','.join('?' * len(seqs))})",
            (chat_id, *seqs)
        )
    conn.close()
