"""Spotting "usage limit reached" in what a brain prints.

The subscriptions (Claude Pro, ChatGPT Plus) have usage limits that reset
after some hours. When one is reached the program says so in its own words,
and BlendWMe offers to hand the work to the other brain.

These patterns come from the messages Claude Code and Codex are known to
print (for example "Claude AI usage limit reached|<time>", "You've hit your
limit · resets 5pm", "You've hit your usage limit ... try again in 2 hours").
They are kept in one place so a message seen in a real run can be added
here. The panel also offers a hand-over for any other error, so an unknown
wording never leaves the user stuck.
"""

import re
import time

PATTERNS = [
    r"usage limit",
    r"hit your (?:\w+ )?limit",
    r"(?:5-hour|five-hour|weekly|daily|session|opus|monthly) limit",
    r"limit (?:reached|exceeded)",
    r"usage_limit_reached",
    r"rate_limit_exceeded",
    r"out of (?:credits|usage|messages)",
    r"quota (?:exceeded|reached)",
]
_RE = re.compile("|".join(PATTERNS), re.I)

# Claude Code's older format: "Claude AI usage limit reached|1760000000" (the reset time in seconds).
_EPOCH = re.compile(r"limit reached\|(\d{9,11})", re.I)
_RESETS = re.compile(r"(resets?(?: at| in)? [^.\n|]{1,40}|try again (?:in|at|after) [^.\n|]{1,40})", re.I)


def is_limit(text, max_length=2000):
    """True when the text is a usage-limit message. Long texts (real replies) never count."""
    text = (text or "").strip()
    if not text or len(text) > max_length:
        return False
    return bool(_RE.search(text))


def reset_hint(text):
    """When the limit resets, in the program's words ("resets 5pm"), or ""."""
    text = text or ""
    m = _EPOCH.search(text)
    if m:
        return "resets " + time.strftime("%H:%M", time.localtime(int(m.group(1))))
    m = _RESETS.search(text)
    return m.group(1).strip(" ·") if m else ""
