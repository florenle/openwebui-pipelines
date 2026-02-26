# lfb_sqlite_docs.py
# Role: CRUD operations for the docs table.
#       Tracks file metadata for uploaded documents. Actual files remain on disk.
#
# Key Functions:
#   add_doc(chat_id, filename): Inserts new doc row, generates doc_id.
#   get_docs_by_chat(chat_id): Returns all docs for a chat ordered by uploaded_at.
#   doc_exists(chat_id, filename): Returns True if filename already tracked for chat.
#   delete_docs_by_chat(chat_id): Removes all doc rows for a chat.
#
# Dependencies:
#   lfb_sqlite: get_conn()
#
# Dev Notes:
#   doc_id is a UUID generated at upload time
#   Files live at chats/chat_<id>/docs/<filename> on disk
#   doc_exists() replaces the filename-keyed skip logic in lfb_OwuiFileHandler.py
#   Deleting a chat cascades to docs rows via foreign key — disk files must be
#   cleaned up separately (handled by retention cron)

import uuid
from datetime import datetime, timezone
from lfb_sqlite import get_conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def add_doc(chat_id, filename):
    conn = get_conn()
    doc_id = str(uuid.uuid4())
    with conn:
        conn.execute(
            """INSERT INTO docs (doc_id, chat_id, filename, uploaded_at)
               VALUES (?, ?, ?, ?)""",
            (doc_id, chat_id, filename, _now())
        )
    conn.close()
    return doc_id


def get_docs_by_chat(chat_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM docs WHERE chat_id = ? ORDER BY uploaded_at", (chat_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def doc_exists(chat_id, filename):
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM docs WHERE chat_id = ? AND filename = ?", (chat_id, filename)
    ).fetchone()
    conn.close()
    return row is not None


def delete_docs_by_chat(chat_id):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM docs WHERE chat_id = ?", (chat_id,))
    conn.close()
