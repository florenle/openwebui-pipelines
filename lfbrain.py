# lfbrain.py
# Status: In Development
# Role: Main pipeline class. Coordinates inlet, pipe, and outlet using lfutils.
#
# Key Functions:
#   inlet(): Copies uploaded files, creates chat in DB, updates title.
#   pipe(): Creates block, submits job, streams status updates, writes system/assistant content.
#   outlet(): Deletes active job on completion.
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

import os
import sys
import uuid
from datetime import datetime
from pydantic import BaseModel
from typing import Iterator

sys.path.append("/app/pipelines/lfutils")
from lfb_OwuiFileHandler import handle_file_uploads
from lfb_sqlite import init_db, create_chat, update_chat_title
from lfb_sqlite_blocks import add_block, update_block_system, update_block_assistant
from lfb_sqlite_jobs import create_job, get_active_job_by_chat, delete_job
from lfb_orchestrator import submit_job, stream_job
from lfb_commands import handle_command
from lfb_log import log


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
        body["lfbrain_chat_id"] = chat_id
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
        log("lfbrain", f"pipe — messages: {body.get('messages', [])[:2]}")
        if not chat_id:
            yield "No chat context found."
            return

        if user_message.strip().startswith("/"):
            log("lfbrain", f"pipe — slash command intercepted: {user_message.strip()}")
            yield from handle_command(user_message.strip(), chat_id, self.valves.openwebui_api_key)
            return

        block_id = str(uuid.uuid4())
        log("lfbrain", f"pipe — new block_id={block_id[:8]}...")
        add_block(chat_id, block_id, user_message)

        try:
            job_id = submit_job(self.orchestrator_url, chat_id)
            create_job(job_id, block_id, chat_id)
            job_submitted_line = f"{self.ts()} ; Job submitted (id: {job_id[:8]}...)\n"
            system_lines: list[str] = []
            system_lines.append(job_submitted_line)
            yield job_submitted_line
        except Exception as e:
            log("lfbrain", f"pipe — orchestrator error: {e}")
            yield f"{self.ts()} ; Orchestrator error: {str(e)}"
            return

        system_lines = []
        assistant_result = None
        try:
            for item in stream_job(self.orchestrator_url, job_id, self.ts):
                if isinstance(item, tuple) and item[0] == "result":
                    assistant_result = item[1]
                    yield assistant_result
                elif isinstance(item, tuple) and item[0] == "failed":
                    assistant_result = f"FAILED: {item[1]}"
                    system_lines.append(f"{self.ts()} ; Failed: {item[1]}\n")
                    yield f"{self.ts()} ; Failed: {item[1]}"
                else:
                    system_lines.append(item)
                    yield item
        except GeneratorExit:
            log("lfbrain", "pipe — GeneratorExit: stream interrupted by user")
            system_lines.append(f"{self.ts()} ; Stream interrupted by user\n")
            assistant_result = assistant_result or "FAILED: interrupted by user"
        finally:
            log("lfbrain", f"pipe — writing system content ({len(system_lines)} lines)")
            update_block_system(block_id, "".join(system_lines))
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
