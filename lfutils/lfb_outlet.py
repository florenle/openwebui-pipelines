# lfb_outlet.py
# Status: Stable
# Role: Captures the completed assistant response and appends it to context.json.
#
# Key Functions:
#   save_assistant_response(chat_dir, chat_id, content): Appends assistant message to context.json.
#
# Dependencies:
#   lfb_context.py
#
# Dev Notes:
#   Called from outlet() in lfbrain.py after pipe() completes.
#   Stores the full assembled assistant response as one clean entry.

from datetime import datetime
from lfb_context import load_context, save_context

def save_assistant_response(chat_dir: str, chat_id: str, content: str):
    context = load_context(chat_dir)
    context["chat_id"] = chat_id
    context["messages"].append({
        "role": "assistant",
        "content": content,
        "timestamp": datetime.now().isoformat()
    })
    save_context(chat_dir, context)

