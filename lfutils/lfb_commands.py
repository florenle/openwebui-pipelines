# lfb_commands.py
# Status: In Development
# Role: Handles slash commands entered by the user in chat.
#
# Key Functions:
#   handle_command(command, chat_id, api_key): Dispatches slash commands, yields response lines.
#   _cmd_info(chat_id): Returns chat_id, title, summary, docs.
#   _cmd_load(parts, chat_id, api_key): Streams chat history as assistant response.
#
# Dependencies:
#   lfb_sqlite: get_chat()
#   lfb_sqlite_docs: get_docs_by_chat()
#   lfb_sqlite_blocks: get_blocks_by_chat()
#   lfb_log: log()
#
# Dev Notes:
#   Called from pipe() in lfbrain.py before orchestrator submission.
#   Returns a generator — must yield at least one line.
#   Slash command turns are never written to DB.
#   api_key: OpenWebUI pipelines API key, used for chat history rewrite in /load -new 1.
#
# /load flags:
#   -v              verbose: show system_content per block
#   -new 0          append, but reset blocks 0-1 first (clean header + load block)
#   -new 1          full rewrite, all context erased
#   default:        -new 1 if target == current chat, -new 0 otherwise

from lfb_sqlite import get_chat
from lfb_sqlite_docs import get_docs_by_chat
from lfb_sqlite_blocks import get_blocks_by_chat
from lfb_log import log

def handle_command(command: str, chat_id: str, api_key: str = ""):
    log("lfb_commands", f"handle_command({command}, chat_id={chat_id})")
    parts = command.strip().split()
    cmd = parts[0].lower()

    if cmd == "/info":
        yield from _cmd_info(chat_id)
        return

    if cmd == "/load":
        yield from _cmd_load(parts, chat_id, api_key)
        return

    log("lfb_commands", f"unknown command: {cmd}")
    yield f"Unknown command: `{command}`\n"
    yield "Available commands: `/info`, `/load`\n"


def _cmd_info(chat_id: str):
    log("lfb_commands", f"_cmd_info({chat_id})")
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


def _cmd_load(parts: list, chat_id: str, api_key: str):
    # Parse flags: /load [-v] [-new 0|1] [target_chat_id]
    verbose = False
    new_level = None  # None = use default
    target_chat_id = chat_id
    args = parts[1:]

    while args:
        if args[0] == "-v":
            verbose = True
            args = args[1:]
        elif args[0] == "-new" and len(args) >= 2 and args[1] in ("0", "1"):
            new_level = int(args[1])
            args = args[2:]
        else:
            target_chat_id = args[0]
            args = args[1:]

    # Apply default: -new 1 if targeting current chat, -new 0 otherwise
    if new_level is None:
        new_level = 1 if target_chat_id == chat_id else 0

    log("lfb_commands", f"_cmd_load(target={target_chat_id}, verbose={verbose}, new_level={new_level})")

    chat = get_chat(target_chat_id)
    if not chat:
        yield f"No chat found for ID: `{target_chat_id}`\n"
        return

    title = chat.get("title") or "Untitled"
    summary = chat.get("summary") or title
    docs = get_docs_by_chat(target_chat_id)
    docs_str = ", ".join(d["filename"] for d in docs) if docs else "none"
    blocks = get_blocks_by_chat(target_chat_id)

    # TODO: -new 0 and -new 1 rewrite logic via OpenWebUI API (api_key) — not yet implemented
    if new_level in (0, 1):
        log("lfb_commands", f"_cmd_load — new_level={new_level} rewrite not yet implemented, falling back to append")

    # Stream output
    yield "***-0-***\n"
    yield f"*Chat ID: {target_chat_id}\n"
    yield f"*Title: {title}\n"
    yield f"*Summary: {summary}\n"
    yield f"*Docs: {docs_str}\n"

    for i, block in enumerate(blocks, start=1):
        yield f"***-{i}-***\n"
        user_content = block.get("user_content") or ""
        system_content = block.get("system_content") or ""
        assistant_content = block.get("assistant_content") or ""
        if user_content:
            yield f"**user:** {user_content}\n"
        if verbose and system_content:
            yield f"**system:** {system_content}\n"
        if assistant_content:
            yield f"**assistant:** {assistant_content}\n"

