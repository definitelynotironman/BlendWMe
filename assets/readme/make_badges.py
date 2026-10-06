# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Draw the README badges in the BlendWMe panel style: paper card, pencil outline,
hard shadow, Patrick Hand label and a tilted Archivo Black sticker for the value.
Text is converted to paths so GitHub shows the real fonts.

    pip install fonttools
    python assets/readme/make_badges.py

Change the version in SPEC below before a new release.
"""
import math, os, random, sys
from xml.sax.saxutils import escape
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FONTS = os.path.join(ROOT, 'BlendWMe-Source', 'blendwme', 'fonts') + os.sep
OUT = os.path.join(HERE, 'badges')


class Face:
    def __init__(self, path):
        self.font = TTFont(path)
        self.gs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font['head'].unitsPerEm
        self.hmtx = self.font['hmtx']

    def width(self, text, size):
        return sum(self.hmtx[self.cmap[ord(c)]][0] for c in text) * size / self.upm

    def path(self, text, size, x, y):
        """SVG path data for text with its baseline at (x, y)."""
        s = size / self.upm
        pen = SVGPathPen(self.gs)
        for c in text:
            g = self.cmap[ord(c)]
            self.gs[g].draw(TransformPen(pen, (s, 0, 0, -s, x, y)))
            x += self.hmtx[g][0] * s
        return pen.getCommands()


HAND = Face(FONTS + 'PatrickHand-Regular.ttf')
BLACK = Face(FONTS + 'ArchivoBlack-Regular.ttf')

THEMES = {
    # Graphite Paper: white card, graphite ink, hard shadow
    'light': dict(card='#FFFFFF', ink='#222222', shadow='#222222', label='#222222'),
    # Blender Grey: dark card, light ink, near-black shadow
    'dark': dict(card='#2B2B2B', ink='#EDEDED', shadow='#111111', label='#EDEDED'),
}


def wobbly_rect(x, y, w, h, r, seed, amp=0.7, step=6):
    """A rounded rectangle traced like a pencil line: points along the outline,
    each nudged by a smooth, small random offset."""
    rnd = random.Random(seed)
    pts = []
    # perimeter walk: straight edges and quarter arcs
    segs = []
    segs.append(('L', (x + r, y), (x + w - r, y)))
    segs.append(('A', (x + w - r, y + r), -90, 0))
    segs.append(('L', (x + w, y + r), (x + w, y + h - r)))
    segs.append(('A', (x + w - r, y + h - r), 0, 90))
    segs.append(('L', (x + w - r, y + h), (x + r, y + h)))
    segs.append(('A', (x + r, y + h - r), 90, 180))
    segs.append(('L', (x, y + h - r), (x, y + r)))
    segs.append(('A', (x + r, y + r), 180, 270))
    for s in segs:
        if s[0] == 'L':
            (x0, y0), (x1, y1) = s[1], s[2]
            n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
            pts += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
        else:
            (cx, cy), a0, a1 = s[1], s[2], s[3]
            n = max(3, int(r * math.pi / 2 / step) + 2)
            pts += [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                     cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n)]
    # smooth noise: random walk, then average with neighbours
    k = len(pts)
    raw = [rnd.uniform(-1, 1) for _ in range(k)]
    sm = [sum(raw[(i + j) % k] for j in range(-3, 4)) / 7 for i in range(k)]
    cx, cy = x + w / 2, y + h / 2
    out = []
    for (px, py), d in zip(pts, sm):
        dx, dy = px - cx, py - cy
        L = math.hypot(dx, dy) or 1
        out.append((px + dx / L * d * amp * 2.2, py + dy / L * d * amp * 2.2))
    return out


def smooth_path(pts, close=True):
    """Catmull-Rom through the points, as cubic Beziers."""
    n = len(pts)
    d = f'M{pts[0][0]:.2f} {pts[0][1]:.2f}'
    for i in range(n if close else n - 1):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f' C{c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {p2[0]:.2f} {p2[1]:.2f}'
    return d + (' Z' if close else '')


def badge(name, label, value, tag, theme, marks=None):
    t = THEMES[theme]
    seed = sum(map(ord, name))
    H = 40                      # card height
    pad = 13
    label_size, value_size = 23, 14.5
    lw = HAND.width(label, label_size)
    # value pieces: text, or ('circle'|'diamond') marks drawn by hand
    pieces = marks or [value]
    vw = 0
    for p in pieces:
        vw += 15 if p in ('circle', 'diamond') else BLACK.width(p, value_size)
    sticker_w = vw + 22
    sticker_h = 26
    W = pad + lw + 12 + sticker_w + 10
    m = 6                       # margin for the shadow and the tilt
    SW, SH = W + m * 2 + 5, H + m * 2 + 5
    x0, y0 = m, m
    card = smooth_path(wobbly_rect(x0, y0, W, H, 10, seed))
    shadow = smooth_path(wobbly_rect(x0 + 4, y0 + 4, W, H, 10, seed + 1))
    ghost = smooth_path(wobbly_rect(x0 + 0.6, y0 - 0.4, W, H, 10, seed + 7, amp=1.0))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{SW:.0f}" height="{SH:.0f}" '
             f'viewBox="0 0 {SW:.1f} {SH:.1f}" role="img" aria-label="{escape(label)}: {escape(value)}">',
             f'<title>{escape(label)}: {escape(value)}</title>',
             f'<path d="{shadow}" fill="{t["shadow"]}"/>',
             f'<path d="{card}" fill="{t["card"]}" stroke="{t["ink"]}" stroke-width="2.4" stroke-linejoin="round"/>',
             f'<path d="{ghost}" fill="none" stroke="{t["ink"]}" stroke-width="0.9" opacity="0.35"/>',
             f'<path d="{HAND.path(label, label_size, x0 + pad, y0 + H / 2 + 7.5)}" fill="{t["label"]}"/>']
    # the sticker, tilted like the WMe tag in the logo
    sx = x0 + pad + lw + 12
    sy = y0 + (H - sticker_h) / 2
    tilt = min(3.0, 190 / sticker_w)
    angle = -tilt if seed % 2 else tilt * 0.85
    ccx, ccy = sx + sticker_w / 2, sy + sticker_h / 2
    st = smooth_path(wobbly_rect(sx, sy, sticker_w, sticker_h, 5, seed + 3, amp=0.45, step=5))
    sts = smooth_path(wobbly_rect(sx + 2.5, sy + 2.5, sticker_w, sticker_h, 5, seed + 4, amp=0.45, step=5))
    g = [f'<g transform="rotate({angle} {ccx:.1f} {ccy:.1f})">',
         f'<path d="{sts}" fill="{t["shadow"]}"/>',
         f'<path d="{st}" fill="{tag}" stroke="{t["ink"]}" stroke-width="2" stroke-linejoin="round"/>']
    x = sx + 11
    base = sy + sticker_h / 2 + 5.3
    for p in pieces:
        if p == 'circle':
            g.append(f'<circle cx="{x + 5.5:.1f}" cy="{base - 5.2:.1f}" r="4.6" fill="none" stroke="#FFFFFF" stroke-width="2.2"/>')
            x += 15
        elif p == 'diamond':
            cx, cy = x + 5.5, base - 5.2
            g.append(f'<path d="M{cx:.1f} {cy - 5.6:.1f} L{cx + 5.6:.1f} {cy:.1f} L{cx:.1f} {cy + 5.6:.1f} '
                     f'L{cx - 5.6:.1f} {cy:.1f} Z" fill="none" stroke="#FFFFFF" stroke-width="2.1" stroke-linejoin="round"/>')
            x += 15
        else:
            g.append(f'<path d="{BLACK.path(p, value_size, x, base)}" fill="#FFFFFF"/>')
            x += BLACK.width(p, value_size)
    g.append('</g>')
    parts += g + ['</svg>']
    os.makedirs(OUT, exist_ok=True)
    with open(f'{OUT}/{name}-{theme}.svg', 'w') as f:
        f.write('\n'.join(parts))


SPEC = [
    ('python', 'Python', '3.10+', '#2D4395', None),
    ('blender', 'Blender', '4.2 LTS & 5.x', '#EE7A1A', None),
    ('brains', 'Brains', 'Claude Code or Codex', '#222222',
     ['circle', ' Claude Code  ', 'diamond', ' Codex']),
    ('version', 'Version', '2.3.0', '#D71920', None),
    ('licence', 'Licence', 'MIT', '#5E5E5E', None),
]
for name, label, value, tag, marks in SPEC:
    for theme in THEMES:
        tg = tag
        if theme == 'dark' and tag == '#222222':
            tg = '#3A3A3A'      # the Blender Grey panel colour, so the black sticker still shows
        badge(name, label, value, tg, theme, marks)
print('done')
