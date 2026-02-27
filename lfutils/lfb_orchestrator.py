# lfb_orchestrator.py
# Status: In Development
# Role: Handles all HTTP communication between the pipeline and lfbrain-orchestrator.
#
# Key Functions:
#   submit_job(orchestrator_url, chat_id): POSTs chat_id to orchestrator, returns job_id.
#   poll_status(orchestrator_url, job_id): GETs current job status and result.
#   stream_job(orchestrator_url, job_id, ts): Generator that yields status log lines.
#
# Dependencies:
#   httpx, time
#   lfb_log: log()
#
# Dev Notes:
#   stream_job handles progress, completed, and failed statuses.
#   progress status yields result field content as progress message.

import time
import httpx
from lfb_log import log


def submit_job(orchestrator_url: str, chat_id: str) -> str:
    log("lfb_orchestrator", f"submit_job(chat_id={chat_id})")
    with httpx.Client() as client:
        response = client.post(
            f"{orchestrator_url}/process",
            json={"chat_id": chat_id},
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


def stream_job(orchestrator_url: str, job_id: str, ts):
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
            elif status == "failed":
                log("lfb_orchestrator", f"stream_job failed: {result}")
                yield ("failed", result or "Unknown error.")
                return
        except Exception as e:
            log("lfb_orchestrator", f"polling error: {e}")
            yield f"{ts()} ; Polling error: {str(e)}"
            return
        time.sleep(1)

