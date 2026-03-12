# lfb_log.py
# Status: In Development
# Role: Shared debug logging utility for all lfbrain modules.
#
# Key Functions:
#   log(module, msg): Prints LFDEBUG line if DEBUG env var is true.
#
# Dependencies:
#   None (stdlib only: os)
#
# Dev Notes:
#   Set DEBUG=true in docker-compose.yml pipelines environment to enable.
#   All modules import log() from here — no direct print() calls.
#
# Schema: LFB03112026A

import os

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"


def log(module: str, msg: str):
    if DEBUG:
        print(f"LFDEBUG [{module}]: {msg}", flush=True)
