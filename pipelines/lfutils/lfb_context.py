# lfb_context.py
# Status: Stable
# Role: Manages reading and writing of context.json, the source of truth for each chat session.
#
# Key Functions:
#   load_context(chat_dir): Loads context.json from disk, returns empty structure if not found.
#   save_context(chat_dir, context): Writes context.json to disk with updated timestamp.
#   update_messages(chat_dir, chat_id, messages): Updates message history in context.json.
#
# Dependencies:
#   None (stdlib only: json, os, datetime)
#
# Dev Notes:
#   context.json lives in chats/chat_<id>/context.json
#   created_at is set only once; last_updated on every save

import json
import os
from datetime import datetime

CONTEXT_FILE = "context.json"

def get_context_path(chat_dir: str) -> str:
    return os.path.join(chat_dir, CONTEXT_FILE)

def load_context(chat_dir: str) -> dict:
    path = get_context_path(chat_dir)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {
        "chat_id": None,
        "messages": [],
        "metadata": {
            "created_at": None,
            "last_updated": None
        }
    }

def save_context(chat_dir: str, context: dict):
    os.makedirs(chat_dir, exist_ok=True)
    now = datetime.now().isoformat()
    context["metadata"]["last_updated"] = now
    if not context["metadata"]["created_at"]:
        context["metadata"]["created_at"] = now
    with open(get_context_path(chat_dir), "w") as f:
        json.dump(context, f, indent=2)

def update_messages(chat_dir: str, chat_id: str, messages: list):
    context = load_context(chat_dir)
    context["chat_id"] = chat_id
    context["messages"] = messages
    save_context(chat_dir, context)