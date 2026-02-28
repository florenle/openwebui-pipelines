# lfb_owui_api.py
# Status: In Development
# Role: OpenWebUI API integration for chat history rewrite.
#       Converts pipeline SQLite blocks into OpenWebUI history format and POSTs to API.
#
# Key Functions:
#   build_history(blocks): Converts pipeline blocks to OpenWebUI linked-list history dict.
#   rewrite_chat_history(chat_id, blocks, api_key): POSTs reconstructed history to OpenWebUI.
#
# Dependencies:
#   requests, uuid
#
# Dev Notes:
#   OpenWebUI internal port is 8080 (mapped to 3000 on host).
#   Each pipeline block maps to one user+assistant message pair.
#   system_content is pipeline-only — never written to OpenWebUI history.
#   Messages are a linked list: null → user1 → assistant1 → user2 → assistant2 → ...
#   currentId = last assistant message id.
#   api_key = pipelines API key passed as Valve from lfbrain.py.
#   blocks must be pre-processed by caller (_cmd_load or equivalent)
#   before passing here. No content logic here — format conversion only.

import uuid
import requests
from lfb_log import log

OWUI_BASE_URL = "http://open-webui:8080/api/v1"

def build_history(blocks: list) -> dict:
    log("lfb_owui_api", f"build_history({len(blocks)} blocks)")
    messages = {}
    prev_id = None
    last_assistant_id = None

    for block in blocks:
        user_id = str(uuid.uuid4())
        assistant_id = str(uuid.uuid4())

        messages[user_id] = {
            "id": user_id,
            "parentId": prev_id,
            "childrenIds": [assistant_id],
            "role": "user",
            "content": block.get("user_content") or "",
            "timestamp": _iso_to_timestamp(block.get("created_at")),
        }

        messages[assistant_id] = {
            "id": assistant_id,
            "parentId": user_id,
            "childrenIds": [],
            "role": "assistant",
            "content": block.get("assistant_content") or "",
            "timestamp": _iso_to_timestamp(block.get("created_at")),
        }

        # Wire previous assistant's childrenIds to this user message
        if prev_id and prev_id in messages:
            messages[prev_id]["childrenIds"] = [user_id]

        prev_id = assistant_id
        last_assistant_id = assistant_id

    return {
        "messages": messages,
        "currentId": last_assistant_id,
    }

def rewrite_chat_history(chat_id: str, blocks: list, api_key: str) -> bool:
    log("lfb_owui_api", f"rewrite_chat_history(chat_id={chat_id}, blocks={len(blocks)})")
    history = build_history(blocks)
    url = f"{OWUI_BASE_URL}/chats/{chat_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"chat": {"history": history}}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            log("lfb_owui_api", "rewrite_chat_history — success")
            return True
        else:
            log("lfb_owui_api", f"rewrite_chat_history — failed {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log("lfb_owui_api", f"rewrite_chat_history — exception: {e}")
        return False

def _iso_to_timestamp(iso_str: str) -> int:
    if not iso_str:
        return 0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp())
    except Exception:
        return 0
