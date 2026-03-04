# lfbrain.py
# Status: In Development
# Role: Main pipeline class. Coordinates inlet, pipe, and outlet using lfutils.
#
# Key Functions:
#   inlet(): Branch-safe sync of SQLite against body["messages"]. Creates chat,
#            updates title, handles file uploads, injects lfbrain_chat_id, strips message ids.
#   pipe(): Creates block, submits job, streams status as <think>...</think>,
#           writes assistant content. Slash commands handled inline.
#   outlet(): True no-op except delete_job().
#
# Dependencies:
#   lfb_OwuiFileHandler, lfb_sqlite, lfb_sqlite_blocks, lfb_sqlite_jobs,
#   lfb_orchestrator, lfb_commands, lfb_log
#
# Dev Notes:
#   pipe() is a sync Generator - async pipe is not supported in pipelines framework.
#   __event_emitter__ is not supported in pipelines framework.
#   stream_job() yields ("result", value) or ("failed", value) sentinels.
#   GeneratorExit handled in pipe() for stop button press.
#   lfbrain_chat_id is injected by inlet() into body — pipe() cannot access chat_id directly.
#   outlet() uses body.get("chat_id") directly — OpenWebUI always provides it there.
#   system_content is ephemeral — streamed as <think>...</think>, never stored.
#   owui_message_id is the sync key for branch reconciliation.
#
# Schema: LFB03042026A

import os
import re
import sys
import uuid
from datetime import datetime
from pydantic import BaseModel
from typing import Iterator

sys.path.append("/app/pipelines/lfutils")
from lfb_OwuiFileHandler import handle_file_uploads
from lfb_sqlite import init_db, create_chat, update_chat_title, clear_chat_summaries
from lfb_sqlite_blocks import (
    add_block,
    update_block_assistant,
    get_blocks_by_chat,
    upsert_block,
    delete_blocks_from_seq,
)
from lfb_sqlite_jobs import create_job, get_active_job_by_chat, delete_job
from lfb_orchestrator import submit_job, stream_job
from lfb_commands import handle_command
from lfb_log import log


def remove_details(content: str) -> str:
    return re.sub(r'<details[^>]*>.*?</details>', '', content, flags=re.DOTALL).strip()


def sync_blocks(chat_id: str, body_messages: list[dict]):
    """Reconcile SQLite blocks against the active branch in body['messages']."""
    owui_pairs = []
    for i in range(0, len(body_messages) - 1, 2):
        user_msg = body_messages[i]
        assistant_msg = body_messages[i + 1]
        owui_pairs.append({
            "owui_message_id": user_msg.get("id"),
            "user_content": user_msg.get("content", ""),
            "assistant_content": remove_details(assistant_msg.get("content", "")),
        })

    db_blocks = get_blocks_by_chat(chat_id)

    divergence = None
    for i, (owui, db) in enumerate(zip(owui_pairs, db_blocks)):
        if owui["owui_message_id"] != db.get("owui_message_id"):
            divergence = i
            break

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
        log("lfbrain", f"sync_blocks — divergence at {divergence}, resynced {len(owui_pairs) - divergence} pairs")
    else:
        log("lfbrain", "sync_blocks — no divergence")


class Pipeline:
    class Valves(BaseModel):
        target_directory: str = "/home/florenle/x/dev/openwebui/chats"
        openwebui_api_key: str = "0p3n-w3bu!"

    def __init__(self):
        init_db()
        self.id = "lfbrain"
        self.name = "Welcome to lfbrain"
        self.valves = self.Valves()
        self.orchestrator_url = "http://lfbrain-orchestrator:8081"

    def ts(self):
        return datetime.now().strftime("%H:%M:%S")

    def get_chat_dir(self, chat_id: str) -> str:
        return os.path.join(self.valves.target_directory, f"chat_{chat_id}")

    async def inlet(self, body: dict, __user__: dict) -> dict:
        chat_id = (
            body.get("chat_id") or body.get("metadata", {}).get("chat_id") or "unknown"
        )
        log("lfbrain", f"inlet(chat_id={chat_id})")

        create_chat(chat_id)

        title = body.get("metadata", {}).get("title")
        if title:
            update_chat_title(chat_id, title)

        handle_file_uploads(
            body.get("files", []),
            "/app/backend/data/uploads",
            self.valves.target_directory,
            chat_id,
        )

        # Sync SQLite against active branch — body["messages"] excludes the new incoming message
        messages = body.get("messages", [])
        if len(messages) >= 2:
            sync_blocks(chat_id, messages[:-1] if len(messages) % 2 == 1 else messages)

        body["lfbrain_chat_id"] = chat_id

        # Strip id from all messages before passing to LLM
        for msg in body.get("messages", []):
            msg.pop("id", None)

        log("lfbrain", f"inlet complete — chat_id={chat_id}, title={title}")
        return body

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: list[dict],
        body: dict,
        __event_emitter__=None,
    ) -> Iterator:
        chat_id = body.get("lfbrain_chat_id")
        log("lfbrain", f"pipe(chat_id={chat_id}, msg={user_message[:40]}...)")
        if not chat_id:
            yield "No chat context found."
            return

        # owui_message_id: last message in body["messages"] is the current user turn
        owui_message_id = None
        raw_messages = body.get("messages", [])
        if raw_messages:
            owui_message_id = raw_messages[-1].get("id")

        block_id = str(uuid.uuid4())
        add_block(chat_id, block_id, owui_message_id, user_message)

        if user_message.strip().startswith("/"):
            log("lfbrain", f"pipe — slash command: {user_message.strip()}")
            result_lines = []
            try:
                for chunk in handle_command(user_message.strip(), chat_id, self.valves.openwebui_api_key):
                    result_lines.append(chunk)
                    yield chunk
            finally:
                update_block_assistant(block_id, "".join(result_lines))
            return

        try:
            job_id = submit_job(self.orchestrator_url, chat_id)
            create_job(job_id, block_id, chat_id)
            job_submitted_line = f"{self.ts()} ; Job submitted (id: {job_id[:8]}...)\n"
            yield f"<think>{job_submitted_line}"
        except Exception as e:
            log("lfbrain", f"pipe — orchestrator error: {e}")
            yield f"<think>{self.ts()} ; Orchestrator error: {str(e)}</think>"
            return

        assistant_result = None
        think_open = True
        try:
            for item in stream_job(self.orchestrator_url, job_id, self.ts):
                if isinstance(item, tuple) and item[0] == "result":
                    if think_open:
                        yield "</think>"
                        think_open = False
                    assistant_result = item[1]
                    yield assistant_result
                elif isinstance(item, tuple) and item[0] == "failed":
                    error_line = f"{self.ts()} ; Failed: {item[1]}\n"
                    if think_open:
                        yield error_line + "</think>"
                        think_open = False
                    else:
                        yield error_line
                    assistant_result = f"FAILED: {item[1]}"
                else:
                    yield item
        except GeneratorExit:
            log("lfbrain", "pipe — GeneratorExit: stream interrupted by user")
            if think_open:
                yield f"{self.ts()} ; Stream interrupted by user\n</think>"
            assistant_result = assistant_result or "FAILED: interrupted by user"
        finally:
            if assistant_result:
                log("lfbrain", f"pipe — writing assistant result len={len(assistant_result)}")
                update_block_assistant(block_id, assistant_result)

    async def outlet(self, body: dict, __user__: dict) -> dict:
        chat_id = body.get("chat_id") or body.get("metadata", {}).get("chat_id")
        log("lfbrain", f"outlet(chat_id={chat_id})")
        if chat_id:
            job = get_active_job_by_chat(chat_id)
            if job:
                delete_job(job["job_id"])
                log("lfbrain", f"outlet — deleted job {job['job_id'][:8]}...")
        return body
