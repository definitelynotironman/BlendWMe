# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Small streaming-safe formatted text layout, shared by readers and chat.

Only paired **bold** and `code` delimiters are interpreted. Incomplete markers
remain literal until paired, so streaming never discards the user's text.
The source is never rewritten. Lines contain measured runs used for drawing.
"""
from functools import lru_cache
import re

__author__ = "definitelynotironman"

_MARKUP = re.compile(r"(`[^`\n]+`|\*\*[^\n]+?\*\*)")
_TOKENS = re.compile(r"\n|[^\S\n]+|[^\s]+")


class RichText:
    """Parses, measures and draws BlendWMe's tiny Markdown subset.

    A run is ``(role, text, advance)``: ``role`` is the font role used to draw it
    (BODY, BLACK for bold, CODE), and ``advance`` is its measured width. Drawing
    reuses the same advances that layout measured, so wrapping, hit testing and
    drawing can never disagree about where a word ends.
    """

    @staticmethod
    @lru_cache(maxsize=128)
    def spans(source):
        """Split source into ``(role, text)`` spans with the markers removed.

        Results are cached because the panel re-lays out the same messages on
        every redraw while the conversation is unchanged.
        """
        result = []
        cursor = 0
        for match in _MARKUP.finditer(source):
            if match.start() > cursor:
                result.append(("BODY", source[cursor:match.start()]))
            value = match.group()
            result.append(("CODE" if value.startswith("`") else "BLACK",
                           value[1:-1] if value.startswith("`") else value[2:-2]))
            cursor = match.end()
        if cursor < len(source):
            result.append(("BODY", source[cursor:]))
        return tuple(result)

    @classmethod
    def plain(cls, source):
        """The visible text without formatting markers (for native labels and copying)."""
        return "".join(text for _, text in cls.spans(source))

    @classmethod
    def layout(cls, source, size, width, measure):
        """Return lines of (font, text, advance). Preserve paragraphs and list markers.

        Break at whitespace when possible, at glyphs for oversized tokens. Each run
        is measured as a whole in its actual font; drawing uses that exact advance.
        ``measure(role, size, text)`` must return the drawn width of ``text``.
        """
        lines, line, used = [], [], 0.0
        for role, text in cls.spans(source):
            for token in _TOKENS.findall(text):
                if token == "\n":
                    lines.append(line)
                    line, used = [], 0.0
                    continue
                advance = measure(role, size, token)
                if used and used + advance > width:
                    lines.append(line)
                    line, used = [], 0.0
                    if token.isspace():
                        continue
                if advance <= width:
                    line.append((role, token, advance))
                    used += advance
                    continue
                # Oversized code/URLs/words still fit the reader without clipping.
                line, used = cls._break_token(role, token, size, width, measure, line, lines)
        lines.append(line)
        return lines

    @staticmethod
    def _break_token(role, token, size, width, measure, line, lines):
        """Split one token wider than ``width`` across lines, glyph by glyph.

        Completed lines are appended to ``lines``; returns the new current line
        and its used width so layout can continue after the token.
        """
        fragment = ""
        used = 0.0
        for char in token:
            trial = measure(role, size, fragment + char)
            if fragment and trial > width:
                line.append((role, fragment, measure(role, size, fragment)))
                lines.append(line)
                line, fragment = [], char
            else:
                fragment += char
        if fragment:
            value = measure(role, size, fragment)
            line.append((role, fragment, value))
            used = value
        return line, used

    @staticmethod
    def draw_line(runs, size, x, y, color):
        """Draw one laid-out line at (x, y). Code runs get a faint background tint."""
        from . import draw2d as d
        for role, text, advance in runs:
            if role == "CODE":
                import gpu
                gpu.state.blend_set("ALPHA")
                d.rect(x, y - size * 0.2, advance, size * 1.25,
                       (color[0], color[1], color[2], 0.08))
            d.text(role, size, x, y, text, color)
            x += advance


# Module-level names used by panel.py, reader.py, ui.py and the tests.
spans = RichText.spans
plain = RichText.plain
layout = RichText.layout
draw_line = RichText.draw_line
