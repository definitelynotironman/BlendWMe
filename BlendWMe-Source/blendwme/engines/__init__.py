"""The brains BlendWMe can work with. Each one implements the small interface in base.py.

    claude_code   Claude Code, signed in with the user's Claude subscription (main brain)
    codex         Codex CLI, signed in with the user's ChatGPT account (second brain)
    api           the 1.0.5 agent loop on a pay-per-token endpoint (off by default)
"""

BRAINS = ("claude", "codex", "api")
