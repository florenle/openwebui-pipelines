# lfb_outlet.py
# Status: In Development
# Role: Captures the completed assistant response and writes it to the DB.
#       Also finalizes the job by deleting the job row on completion.
#
# Key Functions:
#   save_assistant_response(chat_id, content): Updates block with assistant content,
#                                              deletes job row.
#
# Dependencies:
#   lfb_sqlite_blocks: update_block_assistant()
#   lfb_sqlite_jobs: get_active_job_by_chat(), delete_job()
#
# Dev Notes:
#   Called from outlet() in lfbrain.py after pipe() stream is fully assembled.
#   block_id is looked up via active job for this chat_id — no instance state needed.
#   chat_dir no longer needed — all storage is in SQLite.

from lfb_sqlite_blocks import update_block_assistant
from lfb_sqlite_jobs import get_active_job_by_chat, delete_job


def save_assistant_response(chat_id: str, content: str):
    job = get_active_job_by_chat(chat_id)
    if not job:
        return
    update_block_assistant(job["block_id"], content)
    delete_job(job["job_id"])
