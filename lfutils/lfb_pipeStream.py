# lfb_pipeStream.py
# Status: In Development
# Role: Bridges async stream_job_http() into a sync iterator for pipe().
#       Owns all threading, queue, cancel, token buffering, and <think> tag logic.
#       pipe() calls bridge_stream() and consumes normalized (kind, chunk) events.
#
# Public API:
#   bridge_stream(orchestrator_url, query, context, model_hint) -> Iterator[tuple[str, Any]]
#       Yields normalized events: ("think", str), ("token", str), ("usage", dict),
#       ("done", None), ("failed", str).
#       Caller (pipe()) owns the event loop — bridge_stream() handles all threading internally.
#
# Streaming architecture:
#   stream thread → q (unbounded) → relay thread → out_q (maxsize=8) → bridge_stream() yields
#
#   stream thread: runs a new asyncio event loop, consumes stream_job_http(), puts raw
#                  (kind, chunk) tuples into q. Always puts ("done", None) in finally.
#
#   relay thread: moves q → out_q. If out_q stays full for 2s, the consumer (pipe()) has
#                 dropped. Relay cancels the async stream task, drains out_q, then pushes
#                 ("done", None) so pipe() exits cleanly via its normal done branch.
#                 Kill latency: ~2s after consumer dropout, independent of GeneratorExit timing.
#
# Token buffering:
#   token chunks with literal <think> or </think> text are escaped via U+200B between
#   < and the tag name. This prevents tag_output_handler() regex in OpenWebUI from treating
#   them as reasoning block delimiters. A rolling suffix buffer (_THINK_TAG_HOLD chars) catches
#   tags that span two consecutive chunks before they reach the caller.
#
# Dependencies:
#   lfb_orchestrator: stream_job_http()
#   lfb_log: log()
#
# Schema: LFB03112026A

import asyncio
import queue
import threading
from typing import Any, Iterator

from lfb_orchestrator import stream_job_http
from lfb_log import log

# Hold back this many chars in the rolling token buffer to catch cross-chunk <think> patterns.
# len("</think>") - 1 = 7 guarantees any partial tag at a chunk boundary stays buffered.
_THINK_TAG_HOLD = len("</think>") - 1  # 7


def bridge_stream(
    orchestrator_url: str,
    query: str,
    context: str,
    model_hint: str,
) -> Iterator[tuple[str, Any]]:
    """Yield normalized stream events from the orchestrator as a sync iterator.

    Events yielded:
        ("think", str)    — reasoning chunk (before first answer token)
        ("token", str)    — answer token, escaped so <think> tags don't confuse middleware
        ("usage", dict)   — token counts from provider (prompt/completion/total)
        ("done", None)    — stream completed or cancelled cleanly
        ("failed", str)   — unrecoverable error message

    The caller (pipe()) owns all business logic — this function only bridges async → sync
    and handles token escaping. It does not accumulate buffers or write to DB.
    """
    q: queue.Queue = queue.Queue()
    out_q: queue.Queue = queue.Queue(maxsize=8)
    _task_ref: list = []
    _loop_ref: list = []

    def _run_stream():
        """Run async stream_job_http() in a dedicated event loop, put events into q."""
        async def _consume():
            _task_ref.append(asyncio.current_task())
            try:
                async for item in stream_job_http(
                    orchestrator_url,
                    query=query,
                    context=context,
                    model_hint=model_hint,
                ):
                    q.put(item)
            except asyncio.CancelledError:
                log("lfb_pipeStream", "stream task cancelled")
            except Exception as e:
                q.put(("failed", str(e)))
            finally:
                q.put(("done", None))

        loop = asyncio.new_event_loop()
        _loop_ref.append(loop)
        loop.run_until_complete(_consume())

    def _relay():
        """Forward q → out_q. Cancel stream if consumer stops reading for 2s."""
        while True:
            kind, chunk = q.get()
            while True:
                try:
                    out_q.put((kind, chunk), timeout=2.0)
                    break
                except queue.Full:
                    log("lfb_pipeStream", "consumer dropped — cancelling stream")
                    if _loop_ref and _task_ref and not _loop_ref[0].is_closed():
                        _loop_ref[0].call_soon_threadsafe(_task_ref[0].cancel)
                    # Drain out_q then push done so the caller exits via its normal done branch.
                    try:
                        while not out_q.empty():
                            out_q.get_nowait()
                    except Exception:
                        pass
                    out_q.put(("done", None))
                    return
            if kind == "done":
                return

    threading.Thread(target=_run_stream, daemon=True).start()
    threading.Thread(target=_relay, daemon=True).start()

    # Rolling buffer for cross-chunk <think> tag escaping.
    token_pending = ""

    while True:
        kind, chunk = out_q.get()

        if kind == "token":
            # Escape literal <think> / </think> in answer tokens via U+200B so
            # tag_output_handler() regex does not treat them as reasoning delimiters.
            # The rolling suffix buffer catches tags split across chunk boundaries.
            token_pending += chunk
            safe = token_pending.replace("<think>", "<\u200bthink>").replace(
                "</think>", "<\u200b/think>"
            )
            if len(safe) > _THINK_TAG_HOLD:
                yield ("token", safe[:-_THINK_TAG_HOLD])
                token_pending = safe[-_THINK_TAG_HOLD:]
            else:
                token_pending = safe

        elif kind == "done":
            if token_pending:
                yield ("token", token_pending)
                token_pending = ""
            yield ("done", None)
            return

        elif kind == "failed":
            if token_pending:
                yield ("token", token_pending)
                token_pending = ""
            yield ("failed", chunk)
            return

        else:
            # Pass think and usage events through unchanged.
            yield (kind, chunk)
