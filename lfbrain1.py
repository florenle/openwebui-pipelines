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
        # 1. MAGIC FLAG: Setting this to True stops the default RAG/Extraction engine
        self.file_handler = True
        # self.type = "pipe"
        self.id = "lfbrain"
        self.name = "lfbrain test"
        self.valves = self.Valves()
        self.pipelines = [{"id": "lfbrain1", "name": "lfbrain test"}]

    async def inlet(self, body: dict, __user__: dict) -> dict:
        # 1. Extract the top-level files list
        files = body.get("files", [])
        
        # 2. Get Chat ID for folder naming
        chat_id = body.get("chat_id") or body.get("metadata", {}).get("chat_id") or "unknown"
        chat_folder_name = f"chat_{chat_id}"

        for file_item in files:
            # 1. Drill down just to find the ID for path reconstruction
            file_info = file_item.get("file", {})
            file_id = file_info.get("id")
            
            if not file_id:
                continue

            # 2. Reconstruct the path
            upload_dir = "/app/backend/data/uploads"
            try:
                matched = [f for f in os.listdir(upload_dir) if f.startswith(file_id)]
                if matched:
                    full_path = os.path.join(upload_dir, matched[0])
                    
                    # IMPORTANT: Inject the path back into the nested 'file' dict 
                    # so your utility's file_info.get("path") works!
                    file_info["path"] = full_path
                    print(f"DEBUG: Path mapped to {full_path}")
            except Exception as e:
                print(f"DEBUG: Directory scan error: {e}")

            # 3. CALL THE UTILITY WITH THE PARENT (file_item)
            try:
                # We pass file_item because your utility calls .get("file") inside
                saved_path = save_attachment(
                    file_item, self.valves.target_directory, chat_folder_name
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
