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
#   pipe() bridges async stream_job_http() via threading.Thread + two-queue back-pressure design.
#   stream thread → q (unbounded) → relay thread → out_q (maxsize=8) → pipe() yields.
#   relay thread detects consumer dropout via out_q.put(timeout=2.0) → cancels stream task.
#   Kill is independent of GeneratorExit timing — fires ~2s after consumer stops calling next().
#   </think> closed at first token chunk — before GeneratorExit can interfere.
#   lfbrain_chat_id is injected by inlet() into body — pipe() cannot access chat_id directly.
#   outlet() uses body.get("chat_id") directly — OpenWebUI always provides it there.
#   think chunks accumulated and wrapped in <think>...</think> tags.
#   token chunks accumulated for update_block_assistant().
#   model_hint is read from body["model"] in inlet(), stripped of "lfbrain." prefix and context_window
#   suffix (e.g. "lfbrain.local.32768" → "local"), persisted to DB.
#   pipe() reads model_hint from get_chat() — falls back to DEFAULT_MODEL if missing.
#   LFB03102026A: answer_started flag prevents re-opening <think> mid-answer.
#   Providers never resume reasoning_content once content starts — guard is for queue reordering only.
#   Late think chunks are buffered for DB but never yielded as a tag.
#   LFB03102026A: token chunks with literal <think>/<\/think> text are escaped via U+200B
#   to prevent tag_output_handler() regex from treating them as reasoning block delimiters.
#
# Schema: LFB03112026A

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
import tiktoken

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
    sync_blocks,
)
from lfb_sqlite_jobs import get_active_job_by_chat, delete_job
from lfb_orchestrator import stream_job_http, kill_job
from lfb_commands import handle_command
from lfb_log import log

DEFAULT_MODEL = "local"

# Hold back this many chars in the rolling token buffer to catch cross-chunk <think> patterns.
# len("</think>") - 1 = 7 guarantees any partial tag at a chunk boundary stays buffered.
_THINK_TAG_HOLD = len("</think>") - 1  # 7
# Small overhead to approximate system/template tokens not stored in DB
_TOKEN_BOILERPLATE = 20


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
        # Initialize tiktoken encoder for accurate prompt token counts.
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            log("lfbrain", f"tiktoken init failed: {e}")
            self.encoder = None

    def pipelines(self) -> list:
        try:
            with httpx.Client() as client:
                response = client.get(f"{self.orchestrator_url}/models", timeout=5.0)
                response.raise_for_status()
                models = response.json().get("data", [])
                return [
                    {
                        "id": f"{m['id']}.{m.get('context_window', 0)}",
                        "name": m["id"],
                    }
                    for m in models
                ]
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

    def _get_accurate_prompt_tokens(self, chat_id: str, exclude_block_id: str | None = None) -> int:
        """Read full chat history from SQLite and count tokens via tiktoken.

        If `exclude_block_id` is provided, that block will be skipped when counting.
        Returns 0 if encoder unavailable or on error.
        """
        if not self.encoder:
            return 0
        try:
            blocks = get_blocks_by_chat(chat_id)
            full_text_parts = []
            for b in blocks:
                if exclude_block_id and b.get("block_id") == exclude_block_id:
                    continue
                user = b.get("user_content", "") or ""
                assistant = b.get("assistant_content", "") or ""
                if user:
                    full_text_parts.append(f"User: {user}\n")
                if assistant:
                    full_text_parts.append(f"Assistant: {assistant}\n")
            full_text = "".join(full_text_parts)
            toks = self.encoder.encode(full_text)
            return len(toks) + _TOKEN_BOILERPLATE
        except Exception as e:
            log("lfbrain", f"_get_accurate_prompt_tokens failed: {e}")
            return 0

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
        # Strip "lfbrain." prefix and context_window suffix: "lfbrain.local.32768" → "local"
        model_hint = raw.removeprefix("lfbrain.").rsplit(".", 1)[0] or DEFAULT_MODEL
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

        log(
            "lfbrain",
            f"inlet complete — chat_id={chat_id}, title={title}, model_hint={model_hint}",
        )
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
                for chunk in handle_command(
                    user_message.strip(), chat_id, self.valves.openwebui_api_key
                ):
                    result_lines.append(chunk)
                    yield chunk
                try:
                    accurate_pt = self._get_accurate_prompt_tokens(chat_id, exclude_block_id=block_id)
                    answer_text = "".join(result_lines)
                    encoder = getattr(self, "encoder", None)
                    if encoder is not None:
                        try:
                            accurate_ct = len(encoder.encode(answer_text))
                        except Exception as e:
                            log("lfbrain", f"slash answer encode failed: {e}")
                            accurate_ct = max(0, len(answer_text) // 4)
                    else:
                        accurate_ct = max(0, len(answer_text) // 4)

                    _usage = {
                        "prompt_tokens": accurate_pt,
                        "completion_tokens": accurate_ct,
                        "total_tokens": accurate_pt + accurate_ct,
                    }
                    yield {"usage": _usage}
                except Exception as e:
                    log("lfbrain", f"pipe (slash) — failed to compute usage: {e}")
            finally:
                update_block_assistant(block_id, "".join(result_lines))
            return

        model_hint = (get_chat(chat_id) or {}).get("model_hint", DEFAULT_MODEL)
        log("lfbrain", f"pipe — model_hint={model_hint}")

        # Bridge async stream_job_http() into sync pipe() via thread + two-queue back-pressure design.
        #
        # Architecture:
        #   stream thread  →  q (unbounded)  →  relay thread  →  out_q (maxsize=8)  →  pipe() yields
        #
        # Kill mechanism (independent of GeneratorExit timing):
        #   When the consumer stops calling next(), out_q fills up.
        #   relay thread's out_q.put(timeout=2.0) raises queue.Full after 2 seconds.
        #   relay thread calls call_soon_threadsafe(task.cancel) → httpx closes → orchestrator
        #   detects disconnect → stream cancelled → llama-server gets broken pipe → stops.
        q: queue.Queue = queue.Queue()
        out_q: queue.Queue = queue.Queue(maxsize=8)
        _task_ref: list = []
        _loop_ref: list = []

        def _run_stream():
            async def _consume():
                _task_ref.append(asyncio.current_task())
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
            loop.run_until_complete(_consume())

        def _relay():
            """Move q → out_q. If out_q stays full for 2s, consumer dropped — cancel stream."""
            while True:
                kind, chunk = q.get()
                while True:
                    try:
                        out_q.put((kind, chunk), timeout=2.0)
                        break
                    except queue.Full:
                        log("lfbrain", "pipe — relay: consumer dropped, cancelling stream")
                        if _loop_ref and _task_ref and not _loop_ref[0].is_closed():
                            _loop_ref[0].call_soon_threadsafe(_task_ref[0].cancel)
                        return
                if kind == "done":
                    return

        threading.Thread(target=_run_stream, daemon=True).start()
        threading.Thread(target=_relay, daemon=True).start()

        think_buf = []
        answer_buf = []
        think_open = False
        answer_started = False
        token_pending = ""
        llm_usage: dict | None = None

        try:
            while True:
                kind, chunk = out_q.get()

                if kind == "done":
                    if token_pending:
                        yield token_pending
                        token_pending = ""
                    accurate_pt = self._get_accurate_prompt_tokens(chat_id, exclude_block_id=block_id)
                    if llm_usage and llm_usage.get("completion_tokens") is not None:
                        accurate_ct = llm_usage["completion_tokens"]
                    else:
                        answer_text = "".join(think_buf) + "".join(answer_buf)
                        encoder = getattr(self, "encoder", None)
                        if encoder is not None:
                            try:
                                accurate_ct = len(encoder.encode(answer_text))
                            except Exception as e:
                                log("lfbrain", f"answer encode failed: {e}")
                                accurate_ct = max(0, len(answer_text) // 4)
                        else:
                            accurate_ct = max(0, len(answer_text) // 4)

                    _usage = {
                        "prompt_tokens": accurate_pt,
                        "completion_tokens": accurate_ct,
                        "total_tokens": accurate_pt + accurate_ct,
                    }
                    log("lfbrain", f"pipe — usage: {accurate_pt}+{accurate_ct}={accurate_pt + accurate_ct}")
                    yield {"usage": _usage}
                    break

                elif kind == "usage":
                    llm_usage = chunk

                elif kind == "think":
                    think_buf.append(chunk)
                    if not answer_started:
                        if not think_open:
                            yield "<think>"
                            think_open = True
                        yield chunk

                elif kind == "token":
                    if think_open:
                        yield "</think>"
                        think_open = False
                    answer_started = True
                    answer_buf.append(chunk)
                    # Escape literal <think>/</think> in answer tokens via U+200B so
                    # tag_output_handler() regex doesn't treat them as reasoning delimiters.
                    # Rolling suffix buffer (THINK_TAG_HOLD chars) catches tags split across chunks.
                    token_pending += chunk
                    safe = token_pending.replace("<think>", "<\u200bthink>").replace(
                        "</think>", "<\u200b/think>"
                    )
                    if len(safe) > _THINK_TAG_HOLD:
                        yield safe[:-_THINK_TAG_HOLD]
                        token_pending = safe[-_THINK_TAG_HOLD:]
                    else:
                        token_pending = safe

                elif kind == "failed":
                    if token_pending:
                        yield token_pending
                        token_pending = ""
                    if think_open:
                        yield "</think>"
                        think_open = False
                        answer_started = True
                    error_msg = f"{self.ts()} ; Failed: {chunk}"
                    yield error_msg
                    answer_buf.append(f"FAILED: {chunk}")
                    break

        except GeneratorExit:
            # Backup kill path — fires when framework calls .close() on the generator.
            # Primary kill already handled by relay thread after 2s consumer dropout.
            token_pending = ""
            log("lfbrain", "pipe — GeneratorExit: cancelling stream task")
            if _loop_ref and _task_ref and not _loop_ref[0].is_closed():
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
