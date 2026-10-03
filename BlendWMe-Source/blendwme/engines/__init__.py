# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""The brains BlendWMe can work with. Each one implements the small interface in base.py.

    claude_code   Claude Code, signed in with the user's Claude subscription (main brain)
    codex         Codex CLI, signed in with the user's ChatGPT account (second brain)
"""

__author__ = "definitelynotironman"

BRAINS = ("claude", "codex")
