# lfb_sqlite_blocks.py
# Status: In Development
# Role: CRUD operations for the blocks table.
#       Each block represents one user turn: user input + assistant result.
#       Also owns branch reconciliation logic (sync_blocks) since it operates
#       entirely on block data.
#
# Key Functions:
#   add_block(): Inserts new block, auto-assigns next seq.
#   get_block(): Returns block row or None.
#   get_blocks_by_chat(): Returns all blocks for a chat ordered by seq.
#   update_block_assistant(): Stores final LLM result. Accepts incomplete flag for interrupted runs.
#   upsert_block(): Insert or replace by (chat_id, seq).
#   delete_blocks_from_seq(): Deletes all blocks with seq >= value.
#   remove_details(): Strips <details>...</details> tags from content.
#   sync_blocks(): Reconciles SQLite blocks against the active branch in body["messages"].
#                 Side effect: clears chat summaries on divergence.
#
# Dependencies:
#   lfb_sqlite: get_conn(), clear_chat_summaries()
#   lfb_log: log()
#
# Dev Notes:
#   seq is auto-assigned as max(seq)+1 per chat at insert time.
#   block_id is a UUID generated in pipe() at job submission time.
#   owui_message_id is the OpenWebUI user message ID — used as sync key in inlet().
#   think content is streamed live as <think>...</think> tags but not stored — OWUI strips
#   reasoning from assistant messages before sending context back to the LLM.
#   sync_blocks() clears chat summaries when divergence is detected, so downstream
#   summarization is not left holding stale context.
#
# Schema: LFB03112026B

import re
import uuid
from datetime import datetime, timezone
from lfb_sqlite import get_conn, clear_chat_summaries
from lfb_log import log


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def remove_details(content: str) -> str:
    """Strip <details>...</details> blocks from assistant content before sync."""
    return re.sub(r"<details[^>]*>.*?</details>", "", content, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# Block CRUD
# ---------------------------------------------------------------------------

def add_block(chat_id, block_id, owui_message_id=None, user_content=None, assistant_content=None):
    log("lfb_sqlite_blocks", f"add_block({chat_id}, {block_id[:8]}...)")
    conn = get_conn()
    with conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM blocks WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        next_seq = row["next_seq"]
        conn.execute(
            """INSERT INTO blocks (block_id, chat_id, seq, owui_message_id, user_content, assistant_content, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (block_id, chat_id, next_seq, owui_message_id, user_content, assistant_content, _now())
        )
    conn.close()
    log("lfb_sqlite_blocks", f"add_block → seq={next_seq}")
    return next_seq


def get_block(block_id):
    log("lfb_sqlite_blocks", f"get_block({block_id[:8]}...)")
    conn = get_conn()
    row = conn.execute("SELECT * FROM blocks WHERE block_id = ?", (block_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_blocks_by_chat(chat_id):
    log("lfb_sqlite_blocks", f"get_blocks_by_chat({chat_id})")
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM blocks WHERE chat_id = ? ORDER BY seq", (chat_id,)
    ).fetchall()
    conn.close()
    log("lfb_sqlite_blocks", f"get_blocks_by_chat → {len(rows)} blocks")
    return [dict(r) for r in rows]


def update_block_assistant(block_id: str, assistant_content: str, incomplete: bool = False) -> None:
    """Persist the final assistant result for a block. Set incomplete=True for interrupted or failed runs."""
    log("lfb_sqlite_blocks", f"update_block_assistant({block_id[:8]}...) len={len(assistant_content)} incomplete={incomplete}")
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE blocks SET assistant_content = ?, incomplete = ? WHERE block_id = ?",
            (assistant_content, int(incomplete), block_id)
        )
    conn.close()


def upsert_block(chat_id, seq, owui_message_id, user_content, assistant_content):
    log("lfb_sqlite_blocks", f"upsert_block({chat_id}, seq={seq})")
    conn = get_conn()
    with conn:
        conn.execute(
            """INSERT INTO blocks (block_id, chat_id, seq, owui_message_id, user_content, assistant_content, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (chat_id, seq) DO UPDATE SET
                   owui_message_id   = excluded.owui_message_id,
                   user_content      = excluded.user_content,
                   assistant_content = excluded.assistant_content""",
            (str(uuid.uuid4()), chat_id, seq, owui_message_id, user_content, assistant_content, _now())
        )
    conn.close()


def delete_blocks_from_seq(chat_id, seq):
    log("lfb_sqlite_blocks", f"delete_blocks_from_seq({chat_id}, seq>={seq})")
    conn = get_conn()
    with conn:
        conn.execute(
            "DELETE FROM blocks WHERE chat_id = ? AND seq >= ?",
            (chat_id, seq)
        )
    conn.close()


# ---------------------------------------------------------------------------
# Branch reconciliation
# ---------------------------------------------------------------------------

def sync_blocks(chat_id: str, body_messages: list[dict]) -> None:
    """Reconcile SQLite blocks against the active branch in body['messages'].

    Compares OWUI message pairs (user+assistant) against DB blocks in order.
    On first divergence (mismatched owui_message_id), deletes the tail and
    re-upserts from that point. Also clears chat summaries so stale context
    is not reused.
    """
    # Build (owui_message_id, user_content, assistant_content) pairs from OWUI messages.
    owui_pairs = []
    for i in range(0, len(body_messages) - 1, 2):
        user_msg = body_messages[i]
        assistant_msg = body_messages[i + 1]
        owui_pairs.append(
            {
                "owui_message_id": user_msg.get("id"),
                "user_content": user_msg.get("content", ""),
                "assistant_content": remove_details(assistant_msg.get("content", "")),
            }
        )

    db_blocks = get_blocks_by_chat(chat_id)

    # Find first position where OWUI and DB diverge.
    divergence = None
    for i, (owui, db) in enumerate(zip(owui_pairs, db_blocks)):
        if owui["owui_message_id"] != db.get("owui_message_id"):
            divergence = i
            break

    # DB has more blocks than OWUI — tail needs trimming.
    if divergence is None and len(db_blocks) > len(owui_pairs):
        divergence = len(owui_pairs)

    if divergence is not None:
        delete_blocks_from_seq(chat_id, divergence + 1)
        for idx, pair in enumerate(owui_pairs[divergence:]):
            upsert_block(
                chat_id,
                divergence + idx + 1,
                pair["owui_message_id"],
                pair["user_content"],
                pair["assistant_content"],
            )
        clear_chat_summaries(chat_id)
        log(
            "lfb_sqlite_blocks",
            f"sync_blocks — divergence at {divergence}, resynced {len(owui_pairs) - divergence} pairs",
        )
    else:
        log("lfb_sqlite_blocks", "sync_blocks — no divergence")
