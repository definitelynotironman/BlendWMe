"""Small streaming-safe formatted text layout, shared by readers and chat.

Only paired **bold** and `code` delimiters are interpreted. Incomplete markers
remain literal until paired, so streaming never discards the user's text.
The source is never rewritten. Lines contain measured runs used for drawing.
"""
from functools import lru_cache
import re


@lru_cache(maxsize=128)
def spans(source):
    result = []
    cursor = 0
    for match in re.finditer(r"(`[^`\n]+`|\*\*[^\n]+?\*\*)", source):
        if match.start() > cursor:
            result.append(("BODY", source[cursor:match.start()]))
        value = match.group()
        result.append(("CODE" if value.startswith("`") else "BLACK",
                       value[1:-1] if value.startswith("`") else value[2:-2]))
        cursor = match.end()
    if cursor < len(source):
        result.append(("BODY", source[cursor:]))
    return tuple(result)


def plain(source):
    return "".join(text for _, text in spans(source))


def layout(source, size, width, measure):
    """Return lines of (font, text, advance). Preserve paragraphs and list markers.

    Break at whitespace when possible, at glyphs for oversized tokens. Each run
    is measured as a whole in its actual font; drawing uses that exact advance.
    """
    lines, line, used = [], [], 0.0
    for role, text in spans(source):
        for token in re.findall(r"\n|[^\S\n]+|[^\s]+", text):
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
            fragment = ""
            for char in token:
                trial = measure(role, size, fragment + char)
                if fragment and trial > width:
                    value = measure(role, size, fragment)
                    line.append((role, fragment, value))
                    lines.append(line)
                    line, used, fragment = [], 0.0, char
                else:
                    fragment += char
            if fragment:
                value = measure(role, size, fragment)
                line.append((role, fragment, value))
                used += value
    lines.append(line)
    return lines


def draw_line(runs, size, x, y, color):
    from . import draw2d as d
    for role, text, advance in runs:
        if role == "CODE":
            import gpu
            gpu.state.blend_set("ALPHA")
            d.rect(x, y - size * 0.2, advance, size * 1.25,
                   (color[0], color[1], color[2], 0.08))
        d.text(role, size, x, y, text, color)
        x += advance
