import os
import sys
from pathlib import Path
from pydantic import BaseModel
from typing import Union, Generator, Iterator

sys.path.append("/app/pipelines/lfutils")
from lfb_OwuiFileHandler import save_attachment

# Now try the import


class Pipeline:
    class Valves(BaseModel):
        # Set the default path as per your requirement
        target_directory: str = "/home/florenle/x/dev/openwebui/chats"

    def __init__(self):
        # self.type = "pipe"
        self.id = "lfbrain1"
        self.name = "lfbrain test"
        self.valves = self.Valves()
        self.pipelines = [{"id": "lfbrain1", "name": "lfbrain test"}]

    async def inlet(self, body: dict, __user__: dict) -> dict:
        # 1. Look for metadata first, fall back to top-level body
        metadata = body.get("metadata", {})

        # 2. Extract files
        files = metadata.get("files") or body.get("files") or []

        # 3. Extract Chat ID and format it as 'chat_<id>'
        raw_id = metadata.get("chat_id") or body.get("chat_id") or "unknown"
        chat_folder_name = f"chat_{raw_id}"

        # 4. If no files, just move on
        if not files:
            return body

        # 5. Process
        for file_data in files:
            print(f"DEBUG: Processing file in chat {chat_folder_name}")
            try:
                saved_path = save_attachment(
                    file_data, self.valves.target_directory, chat_folder_name
                )
                print(f"File successfully saved to: {saved_path}")
            except Exception as e:
                print(f"Error saving file: {e}")

        return body

    def pipe(
        self, user_message: str, model_id: str, messages: list[dict], body: dict
    ) -> Union[str, Generator, Iterator]:
        # Check if our inlet flagged a missing attachment
        if "No attachment found" in user_message:
            return "Task aborted: No file was provided for processing."

        # Otherwise, proceed with normal logic or agents
        return "File detected. Preparing to process"
