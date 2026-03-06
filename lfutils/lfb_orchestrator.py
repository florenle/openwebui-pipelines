# lfb_orchestrator.py
# Status: In Development
# Role: Handles all HTTP communication between the pipeline and lfbrain-orchestrator.
#
# Key Functions:
#   submit_job(): POSTs job payload to /process, returns job_id. Polling path.
#   poll_status(): GETs current job status and result from /status/{job_id}.
#   stream_job(): Generator that polls /status, yields status log lines and final result. Polling path.
#   stream_job_http(): Async generator. Connects to POST /stream, yields think/token chunks live.
#   kill_job(): POSTs kill signal to /kill/{job_id}.
#
# Dependencies:
#   httpx, time
#   lfb_log: log()
#
# Dev Notes:
#   stream_job_http() is additive — submit_job(), stream_job(), kill_job() polling path stays intact.
#   stream_job_http() is async — pipe() bridges via asyncio.run_coroutine_threadsafe().
#   Client disconnect in pipe() (GeneratorExit) closes httpx stream naturally.
#   SSE format: "data: {json}\n\n". Types: think, token, done, error.
#
# Schema: LFB03052026B

import asyncio
import json
import time
import httpx
from typing import Iterator, AsyncGenerator
from lfb_log import log


def submit_job(
    orchestrator_url: str,
    query: str,
    context: str = "",
    model_hint: str = "local",
) -> str:
    log("lfb_orchestrator", f"submit_job(model_hint={model_hint} query={query[:40]}...)")
    with httpx.Client() as client:
        response = client.post(
            f"{orchestrator_url}/process",
            json={
                "query": query,
                "context": context,
                "model_hint": model_hint,
            },
            timeout=10.0
        )
        job_id = response.json().get("job_id")
        log("lfb_orchestrator", f"submit_job → job_id={job_id[:8] if job_id else None}...")
        return job_id


def poll_status(orchestrator_url: str, job_id: str) -> dict:
    log("lfb_orchestrator", f"poll_status(job_id={job_id[:8]}...)")
    with httpx.Client() as client:
        response = client.get(
            f"{orchestrator_url}/status/{job_id}",
            timeout=30.0
        )
        data = response.json()
        log("lfb_orchestrator", f"poll_status → status={data.get('status')}")
        return data


def kill_job(orchestrator_url: str, job_id: str) -> dict:
    log("lfb_orchestrator", f"kill_job(job_id={job_id[:8]}...)")
    with httpx.Client() as client:
        response = client.post(
            f"{orchestrator_url}/kill/{job_id}",
            timeout=10.0
        )
        data = response.json()
        log("lfb_orchestrator", f"kill_job → {data.get('status')}")
        return data


def stream_job(orchestrator_url: str, job_id: str, ts) -> Iterator:
    log("lfb_orchestrator", f"stream_job(job_id={job_id[:8]}...)")
    last_status = None
    while True:
        try:
            data = poll_status(orchestrator_url, job_id)
            status = data.get("status")
            result = data.get("result")
            if status != last_status:
                yield f"{ts()} ; Status: {status}\n"
                last_status = status
            if status == "progress" and result:
                yield f"{ts()} ; Progress: {result}\n"
            elif status == "completed":
                log("lfb_orchestrator", f"stream_job completed: {result}")
                yield ("result", result or "Done.")
                return
            elif status == "killed":
                log("lfb_orchestrator", f"stream_job killed: job_id={job_id[:8]}")
                yield ("failed", "Job was killed.")
                return
            elif status == "failed":
                log("lfb_orchestrator", f"stream_job failed: {result}")
                yield ("failed", result or "Unknown error.")
                return
        except Exception as e:
            log("lfb_orchestrator", f"polling error: {e}")
            yield f"{ts()} ; Polling error: {str(e)}"
            return
        time.sleep(1)


async def stream_job_http(
    orchestrator_url: str,
    query: str,
    context: str,
    model_hint: str,
) -> AsyncGenerator[tuple[str, str], None]:
    log("lfb_orchestrator", f"stream_job_http start — model_hint={model_hint} query={query[:40]}...")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{orchestrator_url}/stream",
                json={
                    "query": query,
                    "context": context,
                    "model_hint": model_hint,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    kind = data.get("type")
                    chunk = data.get("chunk", "")
                    if kind == "done":
                        log("lfb_orchestrator", "stream_job_http complete")
                        return
                    elif kind == "error":
                        log("lfb_orchestrator", f"stream_job_http error — {chunk}")
                        yield ("failed", chunk)
                        return
                    elif kind in ("think", "token"):
                        yield (kind, chunk)
    except Exception as e:
        log("lfb_orchestrator", f"stream_job_http connection error — {str(e)}")
        yield ("failed", f"Stream connection error: {str(e)}")
    