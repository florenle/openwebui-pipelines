# lfbrain.py
# Status: In Development
# Role: Main pipeline class. Coordinates inlet, pipe, and outlet using lfutils.
#
# Key Functions:
#   inlet(): Copies uploaded files, updates chat in DB with messages and title.
#   pipe(): Creates block, submits job, streams status updates, writes system content.
#   outlet(): Captures assistant response, finalizes block, deletes job row.
#
# Dependencies:
#   lfb_OwuiFileHandler, lfb_sqlite, lfb_sqlite_blocks, lfb_sqlite_jobs,
#   lfb_orchestrator, lfb_outlet, lfb_commands
#
# Dev Notes:
#   pipe() is a sync Generator - async pipe is not supported in pipelines framework.
#   __event_emitter__ is not supported in pipelines framework.
#   block_id looked up in outlet() via active job — no instance state needed.

import os
import sys
import uuid
import time
from datetime import datetime
from pydantic import BaseModel
from typing import Iterator

sys.path.append("/app/pipelines/lfutils")
from lfb_OwuiFileHandler import handle_file_uploads
from lfb_sqlite import init_db
from lfb_sqlite import create_chat, update_chat_title
from lfb_sqlite_blocks import add_block, update_block_system
from lfb_sqlite_jobs import create_job
from lfb_orchestrator import submit_job, stream_job
from lfb_outlet import save_assistant_response
from lfb_commands import handle_command


class Pipeline:
    class Valves(BaseModel):
        target_directory: str = "/home/florenle/x/dev/openwebui/chats"

    def __init__(self):
        init_db()  # LFB02242026B: initialize SQLite DB at pipeline startup
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
        create_chat(chat_id)  # LFB02242026B: no-op if already exists
        title = body.get("metadata", {}).get("title")
        if title:
            update_chat_title(chat_id, title)  # LFB02242026B
        handle_file_uploads(
            body.get("files", []),
            "/app/backend/data/uploads",
            self.valves.target_directory,
            chat_id,
        )
        body["lfbrain_chat_id"] = chat_id
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
        if not chat_id:
            yield "No chat context found."
            return

        # LFB02242026A: intercept slash commands before orchestrator submission
        if user_message.strip().startswith("/"):
            yield from handle_command(user_message.strip(), chat_id, self.get_chat_dir(chat_id))
            return

        # LFB02242026B: create block and job before submitting to orchestrator
        block_id = str(uuid.uuid4())
        add_block(chat_id, block_id, user_message)

        try:
            job_id = submit_job(self.orchestrator_url, chat_id)
            create_job(job_id, block_id, chat_id)
            yield f"{self.ts()} ; Job submitted (id: {job_id[:8]}...)\n"
        except Exception as e:
            yield f"{self.ts()} ; Orchestrator error: {str(e)}"
            return

        # LFB02242026B: accumulate system content, write once at end
        system_lines = []
        try:
            for line in stream_job(self.orchestrator_url, job_id, self.ts):
                system_lines.append(line)
                yield line
        except GeneratorExit:
            system_lines.append(f"{self.ts()} ; Stream interrupted by user\n")
        finally:
            update_block_system(block_id, "".join(system_lines))


    async def outlet(self, body: dict, __user__: dict) -> dict:
        chat_id = (
            body.get("lfbrain_chat_id")
            or body.get("chat_id")
            or body.get("metadata", {}).get("chat_id")
        )
        if not chat_id:
            return body
        assistant_messages = [
            m for m in body.get("messages", []) if m.get("role") == "assistant"
        ]
        if assistant_messages:
            save_assistant_response(
                chat_id,
                assistant_messages[-1].get("content", ""),
            )
        return body
