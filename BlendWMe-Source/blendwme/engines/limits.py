# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
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

__author__ = "definitelynotironman"

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


class LimitDetector:
    """Recognises usage-limit messages and extracts when the limit resets.

    The detector is deliberately conservative about length: a real reply that
    merely mentions "usage limit" (for example a modifier explanation) is long,
    while the programs' limit notices are short.
    """

    # Claude Code's older format: "Claude AI usage limit reached|1760000000" (the reset time in seconds).
    EPOCH = re.compile(r"limit reached\|(\d{9,11})", re.I)
    RESETS = re.compile(r"(resets?(?: at| in)? [^.\n|]{1,40}|try again (?:in|at|after) [^.\n|]{1,40})", re.I)

    def __init__(self, patterns=PATTERNS):
        """Compile the given wording patterns into one case-insensitive regex."""
        self.pattern = re.compile("|".join(patterns), re.I)

    def is_limit(self, text, max_length=2000):
        """True when the text is a usage-limit message. Long texts (real replies) never count."""
        text = (text or "").strip()
        if not text or len(text) > max_length:
            return False
        return bool(self.pattern.search(text))

    def reset_hint(self, text):
        """When the limit resets, in the program's words ("resets 5pm"), or ""."""
        text = text or ""
        m = self.EPOCH.search(text)
        if m:
            return "resets " + time.strftime("%H:%M", time.localtime(int(m.group(1))))
        m = self.RESETS.search(text)
        return m.group(1).strip(" ·") if m else ""


# The adapters call these module functions; they share one detector.
detector = LimitDetector()
is_limit = detector.is_limit
reset_hint = detector.reset_hint
