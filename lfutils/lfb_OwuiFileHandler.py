"""
Purpose: Saves attachments into chat-specific directories.
Input: file_data (dict), base_path (str), chat_id (str)
Output: Full path of the saved file (str)
Structure: <base_path>/chats/<chat_id>/docs/<filename>
"""

import os
import shutil


def save_attachment(file_data: dict, base_path: str, chat_id: str) -> str:
    # 1. Force check the base_path (/home/florenle/x/dev/openwebui/chats)
    # If this was deleted on host, the container mount might be broken
    if not os.path.exists(base_path):
        print(f"DEBUG: Base path {base_path} missing. Attempting to recreate...")
        os.makedirs(base_path, exist_ok=True)

    # 2. Build the target directory: chats/chat_<id>/docs/
    target_dir = os.path.join(base_path, chat_id, "docs")

    # Ensure full path is created
    os.makedirs(target_dir, exist_ok=True)

    # 2. Extract metadata from the CORRECT keys in the debug log
    # Open WebUI puts the meat in the 'file' dictionary
    file_info = file_data.get("file", {})
    filename = file_info.get("filename", "unknown_file")
    source_path = file_info.get("path")

    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(f"Source file not found at {source_path}")

    file_path = os.path.join(target_dir, filename)

    # 3. Copy the file from the Open WebUI upload folder to your chat folder
    shutil.copy2(source_path, file_path)

    return file_path
