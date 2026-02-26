# lfb_sqlite_jobs.py
# Role: CRUD operations for the jobs table.
#       Tracks active job lifecycle. Replaces last.json.
#
# Key Functions:
#   create_job(job_id, block_id, chat_id): Inserts new job row with status=running.
#   get_job(job_id): Returns job row or None.
#   get_job_by_block(block_id): Returns job row for a given block or None.
#   update_job_status(job_id, status, error): Updates status and optionally error.
#   set_killme(job_id): Sets killme=1 for a given job.
#   delete_job(job_id): Removes job row on completion or failure.
#   get_active_job_by_chat(chat_id): Returns most recent job with status='running' for chat.
#
# Dependencies:
#   lfb_sqlite: get_conn()
#
# Dev Notes:
#   Job row is created in pipe() at submission time
#   Orchestrator returns status updates to pipeline via HTTP polling
#   Pipeline writes all status updates to DB — orchestrator never touches DB
#   On job completion or failure, pipeline calls delete_job()
#   killme=1 is set by /kill command — pipeline checks after each status poll

from datetime import datetime, timezone
from lfb_sqlite import get_conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_job(job_id, block_id, chat_id):
    conn = get_conn()
    with conn:
        conn.execute(
            """INSERT INTO jobs (job_id, block_id, chat_id, status, killme, created_at)
               VALUES (?, ?, ?, 'running', 0, ?)""",
            (job_id, block_id, chat_id, _now())
        )
    conn.close()


def get_job(job_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_job_by_block(block_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE block_id = ?", (block_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_job_status(job_id, status, error=None):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE jobs SET status = ?, error = ? WHERE job_id = ?",
            (status, error, job_id)
        )
    conn.close()


def set_killme(job_id):
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE jobs SET killme = 1 WHERE job_id = ?",
            (job_id,)
        )
    conn.close()


def delete_job(job_id):
    conn = get_conn()
    with conn:
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    conn.close()


def get_active_job_by_chat(chat_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM jobs WHERE chat_id = ? AND status = 'running' ORDER BY created_at DESC LIMIT 1",
        (chat_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
