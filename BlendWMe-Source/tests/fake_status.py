"""A fake `claude` / `codex` for the setup-check tests: answers only the status commands.

What it answers comes from the BWM_FAKE_STATUS environment variable (JSON):
    {"version": "...", "help": "...", "auth": {...}, "login": "...", "login_code": 0, "resume_code": 0}
"""

import json
import os
import sys

CLAUDE_HELP = ("-p, --print --output-format --verbose --include-partial-messages --mcp-config --strict-mcp-config "
               "--tools --allowedTools, --allowed-tools --permission-mode --append-system-prompt --session-id "
               "-r, --resume --model --effort")
CODEX_HELP = "--json --skip-git-repo-check -C, --cd -s, --sandbox -c, --config -m, --model"

mode = json.loads(os.environ.get("BWM_FAKE_STATUS", "{}"))
args = sys.argv[1:]
if args == ["--version"]:
    print(mode.get("version", "1.0.0"))
elif args[:2] == ["exec", "resume"]:
    print("Resume a session. PROMPT ... use - to read from stdin")
    sys.exit(mode.get("resume_code", 0))
elif args[-1:] == ["--help"]:
    print(mode.get("help", CODEX_HELP if args[:1] == ["exec"] else CLAUDE_HELP))
elif args[:2] == ["auth", "status"]:
    auth = mode.get("auth", {})
    print(json.dumps(auth))
    sys.exit(0 if auth.get("loggedIn") else 1)
elif args[:2] == ["login", "status"]:
    print(mode.get("login", "Logged in using ChatGPT"))
    sys.exit(mode.get("login_code", 0))
