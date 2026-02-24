# lfbrain.py
# Status: Stable
# Role: Main pipeline class. Coordinates inlet, pipe, and outlet using lfutils.
#
# Key Functions:
#   inlet(): Copies uploaded files, updates context.json with messages.
#   pipe(): Submits job to orchestrator, streams status updates.
#   outlet(): Captures assistant response, appends to context.json.
#
# Dependencies:
#   lfb_OwuiFileHandler, lfb_context, lfb_orchestrator, lfb_outlet
#
# Dev Notes:
#   pipe() is a sync Generator - async pipe is not supported in pipelines framework.
#   __event_emitter__ is not supported in pipelines framework.

import os
import sys
import time
from datetime import datetime
from pydantic import BaseModel
from typing import Iterator

sys.path.append("/app/pipelines/lfutils")
from lfb_OwuiFileHandler import handle_file_uploads
from lfb_context import update_messages
from lfb_orchestrator import submit_job, stream_job
from lfb_outlet import save_assistant_response


class Pipeline:
    class Valves(BaseModel):
        target_directory: str = "/home/florenle/x/dev/openwebui/chats"

    def __init__(self):
        # self.file_handler = True
        # self.pipelines = [{"id": "lfbrain", "name": "lfbrain"}]
        self.id = "lfbrain"
        self.name = "Welcome to lfbrain"
        self.valves = self.Valves()
        self.orchestrator_url = "http://lfbrain-orchestrator:8081"

    def ts(self):
        return datetime.now().strftime("%H:%M:%S")

    def get_chat_dir(self, chat_id: str) -> str:
        return os.path.join(self.valves.target_directory, f"chat_{chat_id}")


    async def inlet(self, body: dict, __user__: dict) -> dict:
        print(f"LFDEBUG: inlet body keys: {list(body.keys())}")
        print(f"LFDEBUG: last message preview: {body.get('messages', [{}])[-1].get('content', '')[:100]}")
    
        chat_id = (
            body.get("chat_id") or body.get("metadata", {}).get("chat_id") or "unknown"
        )
        chat_dir = self.get_chat_dir(chat_id)
        handle_file_uploads(
            body.get("files", []),
            "/app/backend/data/uploads",
            self.valves.target_directory,
            chat_id,
        )
        update_messages(chat_dir, chat_id, body.get("messages", []))
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
        try:
            job_id = submit_job(self.orchestrator_url, chat_id)
            yield f"{self.ts()} ; Job submitted (id: {job_id[:8]}...)\n"
        except Exception as e:
            yield f"{self.ts()} ; Orchestrator error: {str(e)}"
            return
        yield from stream_job(self.orchestrator_url, job_id, self.ts)


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
                self.get_chat_dir(chat_id),
                chat_id,
                assistant_messages[-1].get("content", ""),
            )
        return body
