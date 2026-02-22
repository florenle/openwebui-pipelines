# lfb_orchestrator.py
# Status: Stable
# Role: Handles all HTTP communication between the pipeline and lfbrain-orchestrator.
#
# Key Functions:
#   submit_job(orchestrator_url, chat_id): POSTs chat_id to orchestrator, returns job_id.
#   poll_status(orchestrator_url, job_id): GETs current job status and result.
#   stream_job(orchestrator_url, job_id, ts): Generator that yields status log lines.
#
# Dependencies:
#   httpx, time
#
# Dev Notes:
#   stream_job handles progress, completed, and failed statuses.
#   progress status yields result field content as progress message.

import time
import httpx

def submit_job(orchestrator_url: str, chat_id: str) -> str:
    with httpx.Client() as client:
        response = client.post(
            f"{orchestrator_url}/process",
            json={"chat_id": chat_id},
            timeout=10.0
        )
        return response.json().get("job_id")

def poll_status(orchestrator_url: str, job_id: str) -> dict:
    with httpx.Client() as client:
        response = client.get(
            f"{orchestrator_url}/status/{job_id}",
            timeout=30.0
        )
        return response.json()

def stream_job(orchestrator_url: str, job_id: str, ts):
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
                yield f"{ts()} ; Result: {result or 'Done.'}"
                return
            elif status == "failed":
                yield f"{ts()} ; Failed: {result}"
                return

        except Exception as e:
            yield f"{ts()} ; Polling error: {str(e)}"
            return

        time.sleep(1)