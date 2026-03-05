# lfb_orchestrator.py
# Status: In Development
# Role: Handles all HTTP communication between the pipeline and lfbrain-orchestrator.
#
# Key Functions:
#   submit_job(orchestrator_url, query, context, model_hint): POSTs job payload, returns job_id.
#   poll_status(orchestrator_url, job_id): GETs current job status and result.
#   stream_job(orchestrator_url, job_id, ts): Generator that yields status log lines and final result.
#   kill_job(orchestrator_url, job_id): POSTs kill signal to orchestrator.
#
# Dependencies:
#   httpx, time
#   lfb_log: log()
#
# Dev Notes:
#   stream_job handles progress, completed, failed and killed statuses.
#   kill_job wires to /kill/{job_id} endpoint on orchestrator.
#   model_hint defaults to "local" — chat-scoped model selection TBD via /setmodel.

import time
import httpx
from typing import Iterator
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
    