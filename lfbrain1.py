import os
import sys
import asyncio
import httpx
from pathlib import Path
from pydantic import BaseModel
from typing import Union, Generator, Iterator

sys.path.append("/app/pipelines/lfutils")
from lfb_OwuiFileHandler import save_attachment


class Pipeline:
    class Valves(BaseModel):
        target_directory: str = "/home/florenle/x/dev/openwebui/chats"

    def __init__(self):
        self.file_handler = True
        self.id = "lfbrain"
        self.name = "lfbrain test"
        self.valves = self.Valves()
        self.pipelines = [{"id": "lfbrain1", "name": "lfbrain test"}]
        self.orchestrator_url = "http://lfbrain-orchestrator:8081"

    async def inlet(self, body: dict, __user__: dict) -> dict:
        files = body.get("files", [])
        chat_id = (
            body.get("chat_id") or body.get("metadata", {}).get("chat_id") or "unknown"
        )
        chat_folder_name = f"chat_{chat_id}"

        for file_item in files:
            file_info = file_item.get("file", {})
            file_id = file_info.get("id")
            if not file_id:
                continue

            upload_dir = "/app/backend/data/uploads"
            try:
                matched = [f for f in os.listdir(upload_dir) if f.startswith(file_id)]
                if matched:
                    full_path = os.path.join(upload_dir, matched[0])
                    file_info["path"] = full_path
            except Exception as e:
                print(f"DEBUG: Directory scan error: {e}")

            try:
                saved_path = save_attachment(
                    file_item, self.valves.target_directory, chat_folder_name
                )
                print(f"File successfully saved to: {saved_path}")
                # LF: inject saved_path into body for pipe to use
                body["lfbrain_saved_path"] = saved_path
            except Exception as e:
                print(f"Error saving file: {e}")

        return body


    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: list[dict],
        body: dict,
        __event_emitter__=None,
    ) -> Iterator:
        import time
        from datetime import datetime

        def ts():
            return datetime.now().strftime("%H:%M:%S")

        saved_path = body.get("lfbrain_saved_path")
        if not saved_path:
            yield f"{ts()} ; No file found. Please upload a file."
            return

        # POST to orchestrator
        with httpx.Client() as client:
            try:
                response = client.post(
                    f"{self.orchestrator_url}/process",
                    json={"file_path": saved_path, "query": user_message},
                )
                job_id = response.json().get("job_id")
                yield f"{ts()} ; Job submitted (id: {job_id[:8]}...)\n"
            except Exception as e:
                yield f"{ts()} ; Orchestrator error: {str(e)}"
                return

            # Poll status
            last_status = None
            while True:
                try:
                    response = client.get(
                        f"{self.orchestrator_url}/status/{job_id}", timeout=30.0
                    )
                    data = response.json()
                    status = data.get("status")

                    if status != last_status:
                        yield f"{ts()} ; Status: {status}\n"
                        last_status = status

                    if status == "completed":
                        yield f"{ts()} ; Result: {data.get('result', 'Done.')}"
                        return

                    if status == "failed":
                        yield f"{ts()} ; Failed: {data.get('result')}"
                        return

                except Exception as e:
                    yield f"{ts()} ; Polling error: {str(e)}"
                    return

                time.sleep(1)



        return asyncio.run(_run())
