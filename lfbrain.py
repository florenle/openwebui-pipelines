# lfbrain.py
# Status: In Development
# Role: Main pipeline class. Coordinates inlet, pipe, and outlet using lfutils.
#
# Key Functions:
#   pipelines(): Proxies GET /models from orchestrator. Exposes lfbrain.<model_hint> entries to OWUI.
#   inlet(): Branch-safe sync of SQLite against body["messages"]. Creates chat, updates title,
#            persists model_hint from body["model"], handles file uploads, injects lfbrain_chat_id,
#            strips message ids.
#   pipe(): Creates block, streams tokens live via bridge_stream(). Think and answer chunks
#           yielded immediately. Slash commands handled inline.
#   outlet(): True no-op except delete_job().
#
# Dependencies:
#   lfb_OwuiFileHandler, lfb_sqlite, lfb_sqlite_blocks, lfb_sqlite_jobs,
#   lfb_pipeStream, lfb_commands, lfb_log
#
# Dev Notes:
#   pipe() is a sync Generator — async pipe is not supported in the pipelines framework.
#   __event_emitter__ is not supported in pipelines framework.
#   pipe() calls bridge_stream() (lfb_pipeStream) which owns all threading, queue,
#   cancel, and token buffering logic. pipe() only consumes normalized events.
#   lfbrain_chat_id is injected by inlet() into body — pipe() reads it from body.
#   outlet() uses body.get("chat_id") directly — OpenWebUI always provides it there.
#   model_hint is read from body["model"] in inlet(), stripped of "lfbrain." prefix and
#   context_window suffix (e.g. "lfbrain.local.32768" → "local"), persisted to DB.
#   pipe() reads model_hint from get_chat() — falls back to DEFAULT_MODEL if missing.
#   answer_started flag prevents re-opening <think> mid-answer (LFB03102026A).
#   Usage snapshot emitted at first token so context pie is non-zero on interruption.
#
# Schema: LFB03112026B

import sys
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
)
from lfb_sqlite_blocks import (
    add_block,
    update_block_assistant,
    get_blocks_by_chat,
    sync_blocks,
)
from lfb_sqlite_jobs import get_active_job_by_chat, delete_job
from lfb_commands import handle_command
from lfb_log import log
from lfb_pipeStream import bridge_stream

DEFAULT_MODEL = "local"

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

    def _build_context(self, messages: list[dict]) -> str:
        """Format messages as Role: content transcript for orchestrator context."""
        lines = []
        for msg in messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _compute_usage(
        self,
        chat_id: str,
        block_id: str,
        llm_usage: dict | None,
        answer_buf: list[str],
    ) -> dict:
        """Build a usage dict from provider report when available, tiktoken estimate on interruption.

        Provider llm_usage is accurate — includes orchestrator system prompt and think tokens.
        Tiktoken fallback is used only when llm_usage is absent (e.g. stream interrupted before
        usage event arrives). Fallback undercounts prompt by ~orchestrator system prompt overhead
        but is acceptable for the interrupted case.
        """
        if llm_usage:
            return {
                "prompt_tokens": llm_usage["prompt_tokens"],
                "completion_tokens": llm_usage["completion_tokens"],
                "total_tokens": llm_usage["total_tokens"],
            }

        # Fallback: tiktoken estimate from SQLite history + answer_buf.
        # Used only on interruption when provider usage event has not yet arrived.
        prompt_tokens = 0
        if self.encoder:
            try:
                blocks = get_blocks_by_chat(chat_id)
                parts = []
                for b in blocks:
                    if b.get("block_id") == block_id:
                        continue
                    if b.get("user_content"):
                        parts.append(f"User: {b['user_content']}\n")
                    if b.get("assistant_content"):
                        parts.append(f"Assistant: {b['assistant_content']}\n")
                prompt_tokens = len(self.encoder.encode("".join(parts))) + _TOKEN_BOILERPLATE
            except Exception as e:
                log("lfbrain", f"_compute_usage prompt count failed: {e}")

        answer_text = "".join(answer_buf)
        if self.encoder:
            try:
                completion_tokens = len(self.encoder.encode(answer_text))
            except Exception:
                completion_tokens = max(0, len(answer_text) // 4)
        else:
            completion_tokens = max(0, len(answer_text) // 4)

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

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
                _usage = self._compute_usage(chat_id, block_id, None, result_lines)
                yield {"usage": _usage}
            finally:
                update_block_assistant(block_id, "".join(result_lines))
            return

        model_hint = (get_chat(chat_id) or {}).get("model_hint", DEFAULT_MODEL)
        log("lfbrain", f"pipe — model_hint={model_hint}")

        answer_buf = []
        think_open = False
        answer_started = False
        llm_usage: dict | None = None
        incomplete = False       # set True on failed or interrupted runs for DB flagging
        needs_usage_emit = False  # set True only on GeneratorExit — failed path emits usage inline

        try:
            for kind, chunk in bridge_stream(
                self.orchestrator_url,
                query=user_message,
                context=self._build_context(messages),
                model_hint=model_hint,
            ):
                if kind == "done":
                    _usage = self._compute_usage(chat_id, block_id, llm_usage, answer_buf)
                    log("lfbrain", f"pipe — usage: {_usage['prompt_tokens']}+{_usage['completion_tokens']}={_usage['total_tokens']}")
                    yield {"usage": _usage}
                    break

                elif kind == "usage":
                    llm_usage = chunk

                elif kind == "think":
                    if not answer_started:
                        if not think_open:
                            yield "<think>"
                            think_open = True
                        yield chunk

                elif kind == "token":
                    if think_open:
                        yield "</think>"
                        think_open = False
                    if not answer_started:
                        # Emit usage at first token so context pie is non-zero on interruption.
                        # completion_tokens=0 here; done event updates it accurately on completion.
                        _early_usage = self._compute_usage(chat_id, block_id, llm_usage, [])
                        yield {"usage": _early_usage}
                    answer_started = True
                    answer_buf.append(chunk)
                    # Token escaping and buffering handled by bridge_stream() — yield directly.
                    yield chunk

                elif kind == "failed":
                    if think_open:
                        yield "</think>"
                        think_open = False
                        answer_started = True
                    error_msg = f"{self.ts()} ; Failed: {chunk}"
                    yield error_msg
                    answer_buf.append(f"FAILED: {chunk}")
                    incomplete = True
                    _usage = self._compute_usage(chat_id, block_id, llm_usage, answer_buf)
                    yield {"usage": _usage}
                    break

        except GeneratorExit:
            # Backup kill path — fires when framework calls .close() on the generator.
            # Primary kill handled by bridge_stream()'s relay thread after 2s consumer dropout.
            incomplete = True
            needs_usage_emit = True
            log("lfbrain", "pipe — GeneratorExit")
            if not answer_buf:
                answer_buf.append("FAILED: interrupted by user")

        finally:
            if needs_usage_emit:
                # Emit usage on GeneratorExit — failed path already emits inline.
                _usage = self._compute_usage(chat_id, block_id, llm_usage, answer_buf)
                log("lfbrain", f"pipe — interrupted usage: {_usage['prompt_tokens']}+{_usage['completion_tokens']}")
                yield {"usage": _usage}

            assistant_result = "".join(answer_buf)
            if assistant_result:
                log("lfbrain", f"pipe — writing assistant result len={len(assistant_result)} incomplete={incomplete}")
                update_block_assistant(block_id, assistant_result, incomplete=incomplete)

    async def outlet(self, body: dict, __user__: dict) -> dict:
        chat_id = body.get("chat_id") or body.get("metadata", {}).get("chat_id")
        log("lfbrain", f"outlet(chat_id={chat_id})")
        if chat_id:
            job = get_active_job_by_chat(chat_id)
            if job:
                delete_job(job["job_id"])
                log("lfbrain", f"outlet — deleted job {job['job_id'][:8]}...")
        return body
