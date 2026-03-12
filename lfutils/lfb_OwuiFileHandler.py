# lfb_OwuiFileHandler.py
# Status: In Development
# Role: Handles file copying from OpenWebUI uploads to chat-specific directories.
#
# Key Functions:
#   save_attachment(file_data, base_path, chat_id): Copies a single file to docs/ folder.
#   handle_file_uploads(files, upload_dir, target_dir, chat_id): Handles all files in a request.
#
# Dependencies:
#   os, shutil (stdlib only)
#   lfb_sqlite_docs: doc_exists(), add_doc()
#   lfb_log: log()
#
# Dev Notes:
#   Files are stored at <target_dir>/chat_<chat_id>/docs/<filename>
#   LFB02242026B: filename-exists check replaced with doc_exists() from SQLite
#   add_doc() called after successful copy to track file metadata in DB
#
# Schema: LFB03112026A

import os
import shutil
from lfb_sqlite_docs import doc_exists, add_doc
from lfb_log import log


def save_attachment(file_data: dict, base_path: str, chat_id: str) -> str:
    log("lfb_OwuiFileHandler", f"save_attachment(chat_id={chat_id})")
    if not os.path.exists(base_path):
        os.makedirs(base_path, exist_ok=True)
    target_dir = os.path.join(base_path, chat_id, "docs")
    os.makedirs(target_dir, exist_ok=True)
    file_info = file_data.get("file", {})
    filename = file_info.get("filename", "unknown_file")
    source_path = file_info.get("path")
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(f"Source file not found at {source_path}")
    file_path = os.path.join(target_dir, filename)
    shutil.copy2(source_path, file_path)
    log("lfb_OwuiFileHandler", f"save_attachment → saved to {file_path}")
    return file_path


def handle_file_uploads(files: list, upload_dir: str, target_dir: str, chat_id: str):
    log("lfb_OwuiFileHandler", f"handle_file_uploads(chat_id={chat_id}, {len(files)} files)")
    for file_item in files:
        file_info = file_item.get("file", {})
        file_id = file_info.get("id")
        filename = file_info.get("filename", "unknown_file")
        if not file_id:
            log("lfb_OwuiFileHandler", "skipping file — no file_id")
            continue
        try:
            matched = [f for f in os.listdir(upload_dir) if f.startswith(file_id)]
            if matched:
                file_info["path"] = os.path.join(upload_dir, matched[0])
                log("lfb_OwuiFileHandler", f"matched upload file: {matched[0]}")
        except Exception as e:
            log("lfb_OwuiFileHandler", f"directory scan error: {e}")
        try:
            if not doc_exists(chat_id, filename):
                save_attachment(file_item, target_dir, f"chat_{chat_id}")
                add_doc(chat_id, filename)
                log("lfb_OwuiFileHandler", f"file saved and tracked: {filename}")
            else:
                log("lfb_OwuiFileHandler", f"file already exists, skipping: {filename}")
        except Exception as e:
            log("lfb_OwuiFileHandler", f"error saving file: {e}")
