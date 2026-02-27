# lfb_commands.py
# Status: In Development
# Role: Handles slash commands entered by the user in chat.
#
# Key Functions:
#   handle_command(command, chat_id): Dispatches slash commands, yields response lines.
#
# Dependencies:
#   lfb_sqlite: get_chat()
#   lfb_sqlite_docs: get_docs_by_chat()
#
# Dev Notes:
#   Called from pipe() in lfbrain.py before orchestrator submission.
#   Returns a generator — must yield at least one line.
#   Slash command turns are never written to DB.
#   chat_dir no longer needed — all data from SQLite.

from lfb_sqlite import get_chat
from lfb_sqlite_docs import get_docs_by_chat


def handle_command(command: str, chat_id: str):
    parts = command.strip().split()
    cmd = parts[0].lower()

    if cmd == "/info":
        yield from _cmd_info(chat_id)
        return

    yield f"Unknown command: `{command}`\n"
    yield "Available commands: `/info`\n"


def _cmd_info(chat_id: str):
    chat = get_chat(chat_id)
    if not chat:
        yield f"No chat found for ID: {chat_id}\n"
        return
    title = chat.get("title") or "Untitled"
    summary = chat.get("summary") or title
    docs = get_docs_by_chat(chat_id)
    docs_str = ", ".join(d["filename"] for d in docs) if docs else "none"
    yield f"**Chat ID:** {chat_id}\n"
    yield f"**Title:** {title}\n"
    yield f"**Summary:** {summary}\n"
    yield f"**Docs:** {docs_str}\n"
