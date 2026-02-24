# lfb_commands.py
# Status: Stable
# Role: Handles slash commands entered by the user in chat.
#
# Key Functions:
#   handle_command(command, chat_id, chat_dir): Dispatches slash commands, yields response lines.
#
# Dependencies:
#   lfb_context
#
# Dev Notes:
#   Called from pipe() in lfbrain.py before orchestrator submission.
#   Returns a generator — must yield at least one line.

import os

from lfb_context import load_context

def handle_command(command: str, chat_id: str, chat_dir: str):
    cmd = command.strip().lower()

    if cmd == "/info":
        context = load_context(chat_dir)
        title = context.get("metadata", {}).get("title") or "Untitled"
        summary = context.get("metadata", {}).get("summary") or title
        
        # LFB02242026A: list docs from chat docs/ folder
        docs_dir = os.path.join(chat_dir, "docs")
        if os.path.exists(docs_dir):
            docs = os.listdir(docs_dir)
        else:
            docs = []
        docs_str = ", ".join(docs) if docs else "none"

        yield f"**Chat ID:** {chat_id}\n"
        yield f"**Title:** {title}\n"
        yield f"**Summary:** {summary}\n"
        yield f"**Docs:** {docs_str}\n"
        return

    yield f"Unknown command: `{command}`\n"
    yield "Available commands: `/info`\n"
