# lfbrain.py
# Status: In Development
# Role: Main pipeline class. Coordinates inlet, pipe, and outlet using lfutils.
#
# Key Functions:
#   pipelines(): Proxies GET /models from orchestrator. Exposes lfbrain.<model_hint> entries to OWUI.
#   inlet(): Branch-safe sync of SQLite against body["messages"]. Creates chat, updates title,
#            persists model_hint from body["model"], handles file uploads, injects lfbrain_chat_id,
#            strips message ids.
#   pipe(): Creates block, streams tokens live via stream_job_http(). Think and answer chunks
#           yielded immediately. Slash commands handled inline.
#   outlet(): True no-op except delete_job().
#
# Dependencies:
#   lfb_OwuiFileHandler, lfb_sqlite, lfb_sqlite_blocks, lfb_sqlite_jobs,
#   lfb_orchestrator, lfb_commands, lfb_log
#
# Dev Notes:
#   pipe() is a sync Generator - async pipe is not supported in pipelines framework.
#   __event_emitter__ is not supported in pipelines framework.
#   pipe() bridges async stream_job_http() via threading.Thread + queue.Queue.
#   asyncio.run() in thread creates isolated event loop — no conflict with anyio.
#   GeneratorExit stops queue consumption only — no yield, no loop ops. Thread drains naturally.
#   </think> closed at first token chunk — before GeneratorExit can interfere.
#   lfbrain_chat_id is injected by inlet() into body — pipe() cannot access chat_id directly.
#   outlet() uses body.get("chat_id") directly — OpenWebUI always provides it there.
#   think chunks accumulated and wrapped in <think>...</think> tags.
#   token chunks accumulated for update_block_assistant().
#   model_hint is read from body["model"] in inlet(), stripped of "lfbrain." prefix, persisted to DB.
#   pipe() reads model_hint from get_chat() — falls back to DEFAULT_MODEL if missing.
#
# Schema: LFB03052026B

import asyncio
import os
import queue
import re
import sys
import threading
import uuid
import httpx
from datetime import datetime
from pydantic import BaseModel
from typing import Iterator

sys.path.append("/app/pipelines/lfutils")
from lfb_OwuiFileHandler import handle_file_uploads
from lfb_sqlite import (
    init_db,
    create_chat,
    get_chat,
    update_chat_title,
    update_chat_model_hint,
    clear_chat_summaries,
)
from lfb_sqlite_blocks import (
    add_block,
    update_block_assistant,
    get_blocks_by_chat,
    upsert_block,
    delete_blocks_from_seq,
)
from lfb_sqlite_jobs import get_active_job_by_chat, delete_job
from lfb_orchestrator import stream_job_http, kill_job
from lfb_commands import handle_command
from lfb_log import log

DEFAULT_MODEL = "local"


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
        self.name = "lfbrain-"
        self.type = "manifold"
        self.valves = self.Valves()
        self.orchestrator_url = "http://lfbrain-orchestrator:8081"

    def pipelines(self) -> list:
        try:
            with httpx.Client() as client:
                response = client.get(f"{self.orchestrator_url}/models", timeout=5.0)
                response.raise_for_status()
                models = response.json().get("data", [])
                return [{"id": m['id'], "name": m['id']} for m in models]
        except Exception as e:
            log("lfbrain", f"pipelines() — failed to fetch models: {e}")
            return [{"id": "local", "name": "lfbrain-local"}]

    def ts(self):
        return datetime.now().strftime("%H:%M:%S")

    def get_chat_dir(self, chat_id: str) -> str:
        return os.path.join(self.valves.target_directory, f"chat_{chat_id}")

    def _build_context(self, messages: list[dict]) -> str:
        """Format messages as Role: content transcript for orchestrator context."""
        lines = []
        for msg in messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def inlet(self, body: dict, __user__: dict) -> dict:
        chat_id = (
            body.get("chat_id") or body.get("metadata", {}).get("chat_id") or "unknown"
        )
        log("lfbrain", f"inlet(chat_id={chat_id})")

        create_chat(chat_id)

        title = body.get("metadata", {}).get("title")
        if title:
            update_chat_title(chat_id, title)

        raw = body.get("model", "")
        model_hint = raw.removeprefix("lfbrain.") or DEFAULT_MODEL
        if not model_hint or model_hint == "lfbrain":
            model_hint = DEFAULT_MODEL
        update_chat_model_hint(chat_id, model_hint)
        log("lfbrain", f"inlet — model_hint={model_hint}")

        handle_file_uploads(
            body.get("files", []),
            "/app/backend/data/uploads",
            self.valves.target_directory,
            chat_id,
        )

        messages = body.get("messages", [])
        if len(messages) >= 2:
            sync_blocks(chat_id, messages[:-1] if len(messages) % 2 == 1 else messages)

        body["lfbrain_chat_id"] = chat_id

        incoming = body.get("messages", [])
        if incoming:
            body["lfbrain_owui_message_id"] = incoming[-1].get("id")

        for msg in incoming:
            msg.pop("id", None)

        log("lfbrain", f"inlet complete — chat_id={chat_id}, title={title}, model_hint={model_hint}")
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

        owui_message_id = body.get("lfbrain_owui_message_id")
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

        model_hint = (get_chat(chat_id) or {}).get("model_hint", DEFAULT_MODEL)
        log("lfbrain", f"pipe — model_hint={model_hint}")

        # Bridge async stream_job_http() into sync pipe() via a dedicated thread + queue.
        # Manual loop management (instead of asyncio.run) lets us cancel the task from
        # outside the thread on GeneratorExit, which closes the httpx connection to the
        # orchestrator — Starlette detects the disconnect and cancels stream_job_tokens(),
        # which closes the connection to llama-server (broken pipe → llama-server stops).
        q: queue.Queue = queue.Queue()
        _task_ref: list = []
        _loop_ref: list = []
        _stream_ready = threading.Event()

        def _run_stream():
            async def _consume():
                _task_ref.append(asyncio.current_task())
                _stream_ready.set()
                try:
                    async for item in stream_job_http(
                        self.orchestrator_url,
                        query=user_message,
                        context=self._build_context(messages),
                        model_hint=model_hint,
                    ):
                        q.put(item)
                except asyncio.CancelledError:
                    log("lfbrain", "pipe — stream task cancelled")
                except Exception as e:
                    q.put(("failed", str(e)))
                finally:
                    q.put(("done", None))
            loop = asyncio.new_event_loop()
            _loop_ref.append(loop)
            try:
                loop.run_until_complete(_consume())
            finally:
                loop.close()

        threading.Thread(target=_run_stream, daemon=True).start()

        think_buf = []
        answer_buf = []
        think_open = False

        try:
            while True:
                kind, chunk = q.get()

                if kind == "done":
                    break

                elif kind == "think":
                    if not think_open:
                        yield "<think>"
                        think_open = True
                    think_buf.append(chunk)
                    yield chunk

                elif kind == "token":
                    if think_open:
                        # Close think tag at first token — before any GeneratorExit is possible
                        yield "</think>"
                        think_open = False
                    answer_buf.append(chunk)
                    yield chunk

                elif kind == "failed":
                    if think_open:
                        yield "</think>"
                        think_open = False
                    error_msg = f"{self.ts()} ; Failed: {chunk}"
                    yield error_msg
                    answer_buf.append(f"FAILED: {chunk}")
                    break

        except GeneratorExit:
            # Do not yield here — GeneratorExit forbids it.
            # Cancel the stream task so httpx closes the connection to the orchestrator.
            # Starlette detects client disconnect → cancels stream_job_tokens() →
            # cancels _stream_llamaserver() → closes httpx connection to llama-server →
            # broken pipe on next token write → llama-server stops.
            log("lfbrain", "pipe — GeneratorExit: cancelling stream task")
            _stream_ready.wait(timeout=2)
            if _loop_ref and _task_ref:
                _loop_ref[0].call_soon_threadsafe(_task_ref[0].cancel)
            if not answer_buf:
                answer_buf.append("FAILED: interrupted by user")

        finally:
            think_text = "".join(think_buf)
            answer_text = "".join(answer_buf)
            if think_text:
                assistant_result = f"<think>{think_text}</think>{answer_text}"
            else:
                assistant_result = answer_text
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
