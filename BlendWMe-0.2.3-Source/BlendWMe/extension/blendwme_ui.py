"""BlendWMe 0.2.3 native design shell.
Install the extension ZIP; N > BlendWMe opens this GPU/BLF interface.
Original v0.2 engine callbacks are connected by ui_bridge.py.
Three themes share the same layout, font system and SVG-derived pointer paths.
"""
bl_info = {
    "name": "BlendWMe UI",
    "author": "definitelynotironman",
    "version": (0, 2, 3),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > BlendWMe",
    "description": "Graphite Paper overlay with pencil-style indicators",
    "category": "Interface",
}

import math
import re
from pathlib import Path
import random
import time

import blf
import bpy
import gpu
from bpy.props import StringProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader


# ----------------------------------------------------------------- theme --
def rgba(h, a=1.0):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


def alpha(c, a):
    return (c[0], c[1], c[2], a)


THEMES = {
    'BLENDER': dict(paper='#3A3A3A',card='#232323',ink='#EDEDED',shadow='#151515',muted='#B6B6B6',hot='#F5A623',accent='#EDEDED',on='#141414',hover='#484848',radius=5),
    'GRAPHITE': dict(paper='#F1F1EF',card='#FFFFFF',ink='#222222',shadow='#9A9A9A',muted='#666666',hot='#D71920',accent='#222222',on='#FFFFFF',hover='#E4E4E2',radius=10),
    'RED_WHITE': dict(paper='#FFFFFF',card='#FBEAEC',ink='#141414',shadow='#141414',muted='#666666',hot='#D71920',accent='#D71920',on='#FFFFFF',hover='#F6D5DA',radius=3),
}
FONT_ID = 0
FONTS = {}
UI = {'scale':1.1,'radius':5,'shadow':4,'line':1.8,'reduce_motion':False,'theme':'BLENDER'}
PAPER=CARD=INK=SHADOW=MUTED=HOT=HOVER_LIGHT=HOVER_DARK=ACCENT=ON=(1,1,1,1)

def set_theme(name):
    global PAPER,CARD,INK,SHADOW,MUTED,HOT,HOVER_LIGHT,HOVER_DARK,ACCENT,ON
    theme=THEMES.get(name,THEMES['BLENDER'])
    PAPER,CARD,INK,SHADOW,MUTED,HOT,HOVER_LIGHT,ACCENT,ON=[rgba(theme[k]) for k in ('paper','card','ink','shadow','muted','hot','hover','accent','on')]
    HOVER_DARK=ACCENT
    UI['theme']=name;UI['radius']=theme['radius']

set_theme('BLENDER')

def load_fonts():
    global FONT_ID
    if FONTS:return
    base=Path(__file__).with_name('fonts')
    for key,name in [('body','PatrickHand-Regular.ttf'),('heading','ArchivoBlack-Regular.ttf'),('italic','BlendWMeHand-Oblique.ttf')]:
        FONTS[key]=blf.load(str(base/name))
        if FONTS[key]<0:raise RuntimeError('Cannot load BlendWMe font: '+name)
    FONT_ID=FONTS['body']

def unload_fonts():
    global FONT_ID
    for name in ('PatrickHand-Regular.ttf','ArchivoBlack-Regular.ttf','BlendWMeHand-Oblique.ttf'):
        blf.unload(str(Path(__file__).with_name('fonts')/name))
    FONTS.clear();FONT_ID=0

# ----------------------------------------------------------------- state --
class State:
    def __init__(self):
        self.visible = True
        self.running = False
        self.stop = False
        self.handle = None
        self.mode = "build"          # build | teach | auto
        self.paused = False
        self.prompt = "Add a shade to the lamp"
        self.step_text = "Scale leg 2 down. I will wait."
        self.step = (3, 6)
        self.model_line = "Nemotron 3 \u00b7 Nebius \u00b7 Connected"
        self.log = [
            ("YOU", "Bevel the table top"),
            ("BLENDWME", "Beveled. Legs next."),
            ("YOU", "Paused. Moved leg 2."),
        ]
        self.on_action = None
        self.on_refresh = None
        self.timer = None
        self.wm = None
        self.owner_window = 0
        self.owner_area = 0
        self.token = 0
        self.on_send = None          # hook: function(text)
        self.hits = {}               # region pointer -> [(x, y, w, h, id)]
        self.hover = None
        self.indicators = []
        self.page='main';self.compact=False;self.focus=False;self.buffer='';self.cursor=0;self.select_all=False
        self.flash=None;self.flash_until=0;self.notice='';self.log_scroll=0
        self.s = 1.0                 # current pixel scale

    def push(self, who, msg):
        self.log.append((who, msg))
        del self.log[:-6]
        redraw()

    def set_step(self, text, n, total):
        self.step_text, self.step = text, (n, total)
        redraw()

    def send(self, msg):
        self.push("YOU", msg)
        if self.on_send:
            self.on_send(msg)
        else:
            self.push("BLENDWME", "No model connected yet.")


STATE = State()


def redraw():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


# ------------------------------------------------------------ primitives --
_sh = {}


def shaders():
    if not _sh:
        _sh["fill"] = gpu.shader.from_builtin("UNIFORM_COLOR")
        _sh["line"] = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    return _sh


def v3(pts):
    return [(p[0], p[1], 0.0) for p in pts]


def fill(pts, color):
    sh = shaders()["fill"]
    batch = batch_for_shader(sh, "TRI_FAN", {"pos": v3(pts)})
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def stroke(pts, color, width, closed=False):
    sh = shaders()["line"]
    pts = list(pts)
    if closed:
        pts.append(pts[0])
    batch = batch_for_shader(sh, "LINE_STRIP", {"pos": v3(pts)})
    sh.bind()
    sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    batch.draw(sh)
    if not closed and len(pts)>1:
        for point in (pts[0],pts[-1]):fill(circle_pts(point[0],point[1],width/2,20),color)


def rrect(x, y, w, h, r, seg=24, jit=0.0, seed=0):
    """Rounded rectangle polygon. jit adds a small stable wobble (pencil)."""
    rnd = random.Random(seed)
    r = max(0.5, min(r, w / 2, h / 2))
    pts = []
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0),
                       (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for i in range(seg + 1):
            a = math.radians(a0 + 90 * i / seg)
            pts.append((cx + r * math.cos(a) + rnd.uniform(-jit, jit),
                        cy + r * math.sin(a) + rnd.uniform(-jit, jit)))
    return pts


def circle_pts(cx, cy, r, n=96):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def card(x,y,w,h,bg=None,seed=0,radius=None,sh=0,lw=None,border=None):
    S=STATE.s;r=radius if radius is not None else UI['radius']*S
    bg=CARD if bg is None else bg;border=INK if border is None else border
    if sh:fill(rrect(x+sh*S,y-sh*S,w,h,r),SHADOW)
    fill(rrect(x,y,w,h,r),bg)
    stroke(rrect(x,y,w,h,r),border,(UI['line'] if lw is None else lw)*S,True)

def face(bold=False,italic=False):
    return FONTS.get('italic' if italic else 'heading' if bold else 'body',FONT_ID)

def tw(s,size,bold=False,italic=False):
    font=face(bold,italic);blf.size(font,size*STATE.s)
    return blf.dimensions(font,s)[0]

def text(x,y,s,size,color=None,bold=False,italic=False):
    font=face(bold,italic);blf.size(font,size*STATE.s);blf.color(font,*(INK if color is None else color))
    blf.position(font,x,y,0);blf.draw(font,s)

REFERENCES={'Pause & edit':'resume','Check step':'resume','Apply step':'resume','Resume':'resume','Pause':'pause','Stop':'stop','Modifiers':'MODIFIER','Add Modifier':'MODIFIER','Pin pointer':'pin'}
REF_PATTERN=re.compile(r'(?<!\w)('+'|'.join(re.escape(x) for x in sorted(REFERENCES,key=len,reverse=True))+r')(?!\w)')

def rich_text(x,y,value,size,hits=None):
    cursor=x;last=0
    for match in REF_PATTERN.finditer(value):
        prefix=value[last:match.start()];text(cursor,y,prefix,size);cursor+=tw(prefix,size)
        label=match.group();width=tw(label,size,italic=True)
        text(cursor,y,label,size,italic=True)
        stroke([(cursor,y-3*STATE.s),(cursor+width,y-3*STATE.s)],INK,1*STATE.s)
        if hits is not None:hits.append((cursor,y-5*STATE.s,width,(size+7)*STATE.s,'ref:'+REFERENCES[label]))
        cursor+=width;last=match.end()
    text(cursor,y,value[last:],size)

def wrap(s, size, maxw):
    lines, cur = [], ""
    for word in s.split():
        trial = (cur + " " + word).strip()
        if tw(trial, size) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def fit(s, size, maxw):
    if tw(s, size) <= maxw:
        return s
    while s and tw(s + "\u2026", size) > maxw:
        s = s[:-1]
    return s + "\u2026"


# ----------------------------------------------------------------- panel --
def button(hits,aid,label,x,y,w,h,primary=False,radius=None,seed=0):
    S=STATE.s;hover=STATE.hover==aid
    bg=ACCENT if primary else HOVER_LIGHT if hover else CARD
    fg=ON if primary else INK
    card(x,y,w,h,bg,radius=radius,sh=2 if primary else 0)
    size=14 if primary else 18
    text(x+(w-tw(label,size,bold=primary))/2,y+(h-size*.72*S)/2,label,size,fg,bold=primary)
    hits.append((x,y,w,h,aid))
    if STATE.flash==aid and time.time()<STATE.flash_until:
        pulse=.55+.45*math.sin(time.time()*6)**2 if not UI['reduce_motion'] else 1
        stroke(rrect(x-4*S,y-4*S,w+8*S,h+8*S,UI['radius']*S),alpha(HOT,pulse),2.5*S,True)

def draw_panel(rg,ox,hits,t):
    S=STATE.s;pad=14*S;gap=10*S;pw=340*S
    x0=max(12*S,rg.width-ox-pw-16*S);ix=x0+pad;iw=pw-2*pad;top=rg.height-16*S
    settings=STATE.page=='settings';details=STATE.page=='details'
    compact=STATE.compact and not settings and not details
    body=wrap(STATE.step_text,21,iw-46*S)[:3]
    rows=[34,24,44] if not settings and not details else [34]
    if settings:rows += [30]+[44]*8+[38]
    elif details:rows += [24,176,44,44,44,38]
    else:
        if not compact:rows += [104]
        rows += [65+26*len(body),48,44]
        if not compact:rows += [40]
        rows += [28,34]
    ph=(sum(rows)+10*(len(rows)-1)+28)*S
    card(x0,top-ph,pw,ph,PAPER,sh=UI['shadow'])
    y=top-pad
    def put(h):
        nonlocal y
        y-=h*S;value=y;y-=gap;return value
    yb=put(34)
    text(ix,yb+10*S,'Blend',22,bold=True);bx=ix+tw('Blend',22,bold=True)+3*S
    fill(rrect(bx,yb+4*S,tw('WMe',22,bold=True)+10*S,28*S,3*S),ACCENT)
    text(bx+5*S,yb+10*S,'WMe',22,ON,bold=True)
    button(hits,'settings','⚙' if False else 'Settings',ix+iw-76*S,yb+5*S,76*S,26*S)
    if settings:
        s=bpy.context.scene.bwm
        yb=put(30);text(ix,yb+8*S,'APPEARANCE',13,bold=True)
        for name,label in [('BLENDER','Blender Grey'),('GRAPHITE','Graphite Paper'),('RED_WHITE','Red / White')]:
            yb=put(44);button(hits,'theme:'+name,label,ix,yb,iw,44*S,primary=s.ui_theme==name)
        yb=put(44);button(hits,'scale:-','-',ix,yb,44*S,44*S)
        text(ix+54*S,yb+14*S,'Panel scale: %.2f'%s.graphite_scale,19)
        button(hits,'scale:+','+',ix+iw-44*S,yb,44*S,44*S)
        for aid,label,value in [('motion','Reduced motion',s.reduce_motion),('guides','Blender guides',s.overlay),('compact','Compact panel',STATE.compact)]:
            yb=put(44);button(hits,aid,label+(' · On' if value else ' · Off'),ix,yb,iw,44*S)
        yb=put(44);button(hits,'connection','Connection settings',ix,yb,iw,44*S)
        yb=put(38);button(hits,'back','Back to scene',ix,yb,iw,38*S)
    elif details:
        yb=put(24);text(ix,yb+8*S,'STEP DETAILS',13,bold=True)
        yb=put(176);card(ix,yb,iw,176*S)
        content=getattr(STATE,'detail_text',STATE.step_text)
        lines=wrap(content,18,iw-24*S)
        start=max(0,min(STATE.log_scroll,max(0,len(lines)-7)))
        for i,line in enumerate(lines[start:start+7]):rich_text(ix+12*S,yb+151*S-i*22*S,line,18,hits)
        hits.append((ix,yb,iw,176*S,'scroll-details'))
        for aid,label in [('pin','Pin pointer in Blender'),('unpin','Reset pointers'),('skip','Keep my edit / Skip')]:
            yb=put(44);button(hits,aid,label,ix,yb,iw,44*S)
        yb=put(38);button(hits,'back','Back to scene',ix,yb,iw,38*S)
    else:
        yb=put(24)
        fill(circle_pts(ix+5*S,yb+12*S,4*S),HOT if STATE.activity_label in ('Thinking','Building') else INK)
        text(ix+17*S,yb+7*S,STATE.activity_label,17)
        yb=put(44);seg=iw/3
        for i,(key,label) in enumerate((('build','Build'),('teach','Teach'),('auto','Auto'))):
            button(hits,'tab:'+key,label,ix+i*seg,yb,seg,44*S,primary=STATE.mode==key)
        if not compact:
            yb=put(104);card(ix,yb,iw,104*S)
            log=STATE.log[-3:] if STATE.log_scroll==0 else STATE.log[max(0,len(STATE.log)-3-STATE.log_scroll):max(0,len(STATE.log)-STATE.log_scroll)]
            for i,(who,msg) in enumerate(log):
                base=yb+78*S-i*28*S
                text(ix+10*S,base,who,11,bold=True);px=ix+10*S+tw(who,11,bold=True)+6*S
                rich_text(px,base,fit(msg,17,ix+iw-10*S-px),17,hits)
            hits.append((ix,yb,iw,104*S,'scroll-log'))
        h=65+26*len(body);yb=put(h);card(ix,yb,iw,h*S,border=HOT if UI['theme']=='RED_WHITE' else INK)
        on=UI['reduce_motion'] or int(t*2)%2==0
        fill(rrect(ix+12*S,yb+(h-32)*S,18*S,18*S,1*S),alpha(HOT,1 if on else .18) if STATE.attention else alpha(HOT,.35))
        label=fit(getattr(STATE,'step_label','YOUR TURN'),12,iw-46*S)
        text(ix+40*S,yb+(h-26)*S,label,12,bold=True)
        for i,line in enumerate(body):rich_text(ix+40*S,yb+(h-56-i*26)*S,line,21,hits)
        text(ix+iw-40*S,yb+9*S,'Why?',15,italic=True)
        stroke([(ix+iw-40*S,yb+6*S),(ix+iw-9*S,yb+6*S)],INK,S)
        hits.append((ix+iw-48*S,yb,48*S,27*S,'details'))
        hits.append((ix,yb,iw,h*S,'details'))
        yb=put(48);bw=(iw-8*S)/2
        button(hits,'resume',STATE.primary_label,ix,yb,bw,48*S,True)
        button(hits,'pause',STATE.secondary_label,ix+bw+8*S,yb,bw,48*S)
        yb=put(44);card(ix,yb,iw,44*S)
        shown=STATE.buffer if STATE.focus else STATE.prompt or 'Ask or change the plan…'
        if STATE.focus:
            shown=shown[max(0,STATE.cursor-35):STATE.cursor]+('│' if UI['reduce_motion'] or int(t*2)%2==0 else '')+shown[STATE.cursor:]
        text(ix+10*S,yb+14*S,fit(shown,18,iw-48*S),18,MUTED if not STATE.prompt and not STATE.focus else INK)
        hits.append((ix,yb,iw-38*S,44*S,'input'))
        button(hits,'send','',ix+iw-38*S,yb,38*S,44*S)
        fill([(ix+iw-27*S,yb+10*S),(ix+iw-9*S,yb+22*S),(ix+iw-27*S,yb+34*S),(ix+iw-22*S,yb+22*S)],ACCENT)
        if not compact:
            yb=put(40);cw=(iw-12*S)/3
            for i,name in enumerate(('Lamp','Desk','Bevel')):button(hits,'chip:'+name,name,ix+i*(cw+6*S),yb,cw,40*S,radius=20*S)
        yb=put(28)
        indicator=HOT if STATE.connection_verified else INK
        if 'Not verified' in STATE.model_line:stroke(circle_pts(ix+5*S,yb+13*S,4*S),indicator,1.5*S,True)
        else:fill(circle_pts(ix+5*S,yb+13*S,4*S),indicator)
        text(ix+16*S,yb+8*S,fit(STATE.model_line,15,iw-16*S),15)
        hits.append((ix,yb,iw,28*S,'connection'))
        yb=put(34);cw=(iw-8*S)/2
        button(hits,'stop','Stop',ix,yb,cw,34*S)
        button(hits,'compact','Expand' if compact else 'Compact',ix+cw+8*S,yb,cw,34*S)
    if STATE.notice:
        text(ix,top-ph-24*S,fit(STATE.notice,16,iw),16,HOT)
    hits.append((x0,top-ph,pw,ph,'panel'))

# ------------------------------------------------------------ indicators --
def indicate(kind, text="", obj=None, xy=None, rect=None, ttl=0):
    """kind: 'block' (blinking red block), 'radar' (radiating rings),
    'circle' (pencil circle + arrow around rect=(x, y, w, h) in region pixels,
    or around an object if obj is given). ttl = seconds, 0 = stay."""
    STATE.indicators.append(dict(kind=kind, text=text, obj=obj, xy=xy,
                                 rect=rect, t0=time.time(), ttl=ttl))
    redraw()


def clear():
    STATE.indicators.clear()
    redraw()


def tag(x, y, msg, seed=11):
    S = STATE.s
    card(x, y, tw(msg, 18) + 24 * S, 34 * S, CARD, seed=seed, radius=8 * S, sh=3)
    text(x + 12 * S, y + 10 * S, msg, 18)


def bezier(p0, p1, p2, n=48):
    out = []
    for i in range(n + 1):
        u = i / n
        out.append(((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0],
                    (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1]))
    return out


_svg_paths=None

def svg_arrow(end,S):
    """Sample the shipped cubic SVG path into smooth native GPU strokes."""
    global _svg_paths
    if _svg_paths is None:
        import xml.etree.ElementTree as ET
        path=ET.parse(Path(__file__).with_name('assets')/'pencil-arrow.svg').getroot()[0].attrib['d']
        tokens=re.findall(r'[MCL]|-?\d+(?:\.\d+)?',path);i=0;cursor=(0,0);paths=[];points=[]
        while i<len(tokens):
            command=tokens[i];i+=1
            count=6 if command=='C' else 2;v=[float(t) for t in tokens[i:i+count]];i+=count
            if command=='M':
                if points:paths.append(points)
                cursor=(v[0],v[1]);points=[cursor]
            elif command=='L':cursor=(v[0],v[1]);points.append(cursor)
            else:
                p0=cursor;p1=(v[0],v[1]);p2=(v[2],v[3]);p3=(v[4],v[5])
                for n in range(1,65):
                    t=n/64;u=1-t;points.append((u**3*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*p3[0],u**3*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*p3[1]))
                cursor=p3
        if points:paths.append(points)
        _svg_paths=paths
    for path in _svg_paths:
        stroke([(end[0]+(x-8)*S*1.3,end[1]+(52-y)*S*1.3) for x,y in path],HOT,3*S)

def wobble_ellipse(cx, cy, rx, ry, seed=1.0):
    pts, n = [], 180
    for i in range(n + 1):
        a = -0.3 + (2 * math.pi + 0.65) * i / n   # small overshoot, like a pencil
        k = 1 + 0.05 * math.sin(3 * a + seed) + 0.03 * math.sin(5 * a + 2 * seed)
        pts.append((cx + rx * k * math.cos(a), cy + ry * k * math.sin(a)))
    return pts


def anchor(ind, rg, rv):
    if ind["obj"]:
        ob = bpy.data.objects.get(ind["obj"])
        if ob is None or rv is None:
            return None
        p = view3d_utils.location_3d_to_region_2d(rg, rv, ob.matrix_world.translation)
        return (p.x, p.y) if p else None
    if ind["rect"]:
        x, y, w, h = ind["rect"]
        return (x + w / 2, y + h / 2)
    return ind["xy"]


def draw_indicator(ind, rg, rv, t):
    p = anchor(ind, rg, rv)
    if p is None:
        return
    S, (x, y), age = STATE.s, p, t - ind["t0"]
    kind = ind["kind"]
    if kind == "block":
        size = 26 * S
        on = UI["reduce_motion"] or int(age * 2) % 2 == 0
        fill(rrect(x - size / 2, y - size / 2, size, size, 2 * S, 1), HOT if on else alpha(HOT, 0.12))
        stroke(rrect(x - size / 2, y - size / 2, size, size, 2 * S, 1, jit=0.0, seed=3), INK, 2.5 * S, True)
        if ind["text"]:
            tag(x + size, y - 17 * S, ind["text"])
    elif kind == "radar":
        for k in range(2):
            ph = (age / 1.8 + k * 0.5) % 1.0
            if UI["reduce_motion"]:
                ph = 0.5 + 0.25 * k
            cap=max(6,min(32*S,rg.width/2-3,rg.height/2-3))
            stroke(circle_pts(x,y,cap*(.45+.55*ph)),alpha(HOT,.9*(1-ph)),2*S,True)
        stroke(circle_pts(x,y,min(12*S,max(6,rg.width/2-3))),HOT,2*S,True)  # Keep the actual icon visible.
        if ind["text"]:
            tag(x + 20 * S, y + 22 * S, ind["text"])
    elif kind == "circle":
        if ind["rect"]:
            rx, ry = ind["rect"][2] / 2 * 1.3 + 10 * S, ind["rect"][3] / 2 * 1.3 + 10 * S
        else:
            rx = ry = 42 * S
        prog = 1.0 if UI["reduce_motion"] else min(1.0, age / 0.6)   # pencil draws itself
        pts = wobble_ellipse(x, y, rx, ry)
        stroke(pts[:max(2, int(len(pts) * prog))], HOT, 4 * S)
        if prog >= 1.0:
            ang = math.radians(40)
            end = (x + rx * 1.1 * math.cos(ang), y + ry * 1.1 * math.sin(ang))
            start = (end[0] + 80 * S, end[1] + 70 * S)
            ctrl = (start[0] - 10 * S, end[1] + 8 * S)
            svg_arrow(end,S)
            if ind["text"]:
                tag(start[0] - 10 * S, start[1] + 4 * S, ind["text"])


# --------------------------------------------------------------- drawing --
def draw_cb():
    ctx = bpy.context
    rg = ctx.region
    if not STATE.visible or rg is None or rg.type != "WINDOW":
        return
    if STATE.owner_window and (ctx.window.as_pointer() != STATE.owner_window
                               or ctx.area.as_pointer() != STATE.owner_area):
        return
    if STATE.on_refresh:
        STATE.on_refresh(ctx)
    STATE.s = ctx.preferences.system.ui_scale * UI["scale"]
    t = time.time()
    STATE.indicators[:] = [i for i in STATE.indicators if not i["ttl"] or t - i["t0"] < i["ttl"]]
    ox = 0
    for r in ctx.area.regions:
        if r.type == "UI" and r.width > 1:
            ox = r.width            # keep the panel clear of the sidebar
    # Fit the same design into the current viewport without changing Blender's theme.
    count=len(wrap(STATE.step_text,21,266*STATE.s)[:3])
    estimate=582 if STATE.page=='settings' else 492 if STATE.page=='details' else (519 if STATE.compact else 683)+26*count
    STATE.s=min(STATE.s,max(.35,(rg.height-44)/estimate),max(.35,(rg.width-ox-32)/340))
    hits = []
    gpu.state.blend_set("ALPHA")
    try:
        for ind in STATE.indicators:
            draw_indicator(ind, rg, ctx.region_data, t)
        draw_panel(rg, ox, hits, t)
        if STATE.mode=='auto' and STATE.page=='main':
            value='Pause & edit hands Blender back to you.'
            width=tw(value,17)+24*STATE.s
            card(18,18,width,34*STATE.s,PAPER,sh=3)
            rich_text(18+12*STATE.s,18+10*STATE.s,value,17,hits)
    finally:
        gpu.state.blend_set("NONE")
    STATE.hits[rg.as_pointer()] = hits


# ------------------------------------------------------------- operators --
CHIP_PROMPTS = {"Lamp": "Build a lamp", "Desk": "Build a desk", "Bevel": "Bevel selected"}


def find_hit(ctx, ev):
    for area in ctx.window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for r in area.regions:
            if r.type == "WINDOW" and r.x <= ev.mouse_x < r.x + r.width \
                    and r.y <= ev.mouse_y < r.y + r.height:
                mx, my = ev.mouse_x - r.x, ev.mouse_y - r.y
                for x, y, w, h, aid in STATE.hits.get(r.as_pointer(), []):
                    if x <= mx <= x + w and y <= my <= y + h:
                        return aid
    return None


def run_action(aid):
    if STATE.on_action:
        STATE.on_action(aid)
        redraw()
        return
    if aid.startswith("tab:"):
        STATE.mode = aid[4:]
    elif aid == "resume":
        STATE.paused = False
        STATE.push("BLENDWME", "Resumed. Reading your edits.")
    elif aid == "pause":
        STATE.paused = True
        STATE.push("BLENDWME", "Paused. Edit freely.")
    elif aid == "input":
        bpy.ops.bwm.prompt("INVOKE_DEFAULT")
    elif aid.startswith("chip:"):
        STATE.send(CHIP_PROMPTS[aid[5:]])
    redraw()


class BWM_OT_overlay(bpy.types.Operator):
    bl_idname = "bwm.overlay"
    bl_label = "Show / hide BlendWMe"

    def invoke(self, ctx, ev):
        if STATE.running:
            close_overlay()
            return {"FINISHED"}
        load_fonts()
        STATE.running, STATE.stop = True, False
        STATE.token += 1
        self._token = STATE.token
        STATE.owner_window = ctx.window.as_pointer()
        STATE.owner_area = ctx.area.as_pointer()
        STATE.wm = ctx.window_manager
        STATE.handle = bpy.types.SpaceView3D.draw_handler_add(draw_cb, (), "WINDOW", "POST_PIXEL")
        STATE.timer = ctx.window_manager.event_timer_add(1 / 30, window=ctx.window)
        ctx.window_manager.modal_handler_add(self)
        redraw()
        return {"RUNNING_MODAL"}

    def modal(self, ctx, ev):
        if STATE.stop or self._token != STATE.token:
            return {"CANCELLED"}
        if not any(a.as_pointer()==STATE.owner_area for a in ctx.window.screen.areas):
            close_overlay();return {'CANCELLED'}
        if ev.type == "TIMER":
            redraw()
            return {"PASS_THROUGH"}
        aid = find_hit(ctx, ev)
        if STATE.focus and ev.value=='PRESS':
            if ev.type=='ESC':STATE.focus=False;redraw();return {'RUNNING_MODAL'}
            if ev.type in {'RET','NUMPAD_ENTER'}:
                STATE.prompt=STATE.buffer;STATE.focus=False;STATE.send(STATE.buffer);redraw();return {'RUNNING_MODAL'}
            if ev.ctrl and ev.type=='A':STATE.select_all=True;return {'RUNNING_MODAL'}
            if ev.type in {'LEFT_ARROW','RIGHT_ARROW','HOME','END'}:
                STATE.cursor=0 if ev.type=='HOME' else len(STATE.buffer) if ev.type=='END' else max(0,min(len(STATE.buffer),STATE.cursor+(-1 if ev.type=='LEFT_ARROW' else 1)))
                STATE.select_all=False;redraw();return {'RUNNING_MODAL'}
            value=bpy.context.window_manager.clipboard if ev.ctrl and ev.type=='V' else getattr(ev,'unicode','') if not ev.ctrl and not ev.alt else ''
            if value or ev.type in {'BACK_SPACE','DEL'}:
                if STATE.select_all:STATE.buffer='';STATE.cursor=0;STATE.select_all=False
                if value:
                    value=value.replace('\n',' ')[:4000];STATE.buffer=(STATE.buffer[:STATE.cursor]+value+STATE.buffer[STATE.cursor:])[:4000];STATE.cursor=min(len(STATE.buffer),STATE.cursor+len(value))
                elif ev.type=='BACK_SPACE' and STATE.cursor>0:STATE.buffer=STATE.buffer[:STATE.cursor-1]+STATE.buffer[STATE.cursor:];STATE.cursor-=1
                elif ev.type=='DEL':STATE.buffer=STATE.buffer[:STATE.cursor]+STATE.buffer[STATE.cursor+1:]
                redraw();return {'RUNNING_MODAL'}
        if aid and ev.type in {'WHEELUPMOUSE','WHEELDOWNMOUSE'}:
            STATE.log_scroll=max(0,STATE.log_scroll+(1 if ev.type=='WHEELUPMOUSE' else -1));redraw();return {'RUNNING_MODAL'}
        if ev.type=='LEFTMOUSE' and ev.value=='PRESS' and aid not in {'input','send'}:STATE.focus=False
        if ev.type == "MOUSEMOVE":
            new = None if aid == "panel" else aid
            if new != STATE.hover:
                STATE.hover = new
                redraw()
            return {"PASS_THROUGH"}
        if aid and ev.type in {"LEFTMOUSE", "RIGHTMOUSE"}:
            if ev.type == "LEFTMOUSE" and ev.value == "PRESS" and aid != "panel":
                run_action(aid)
            return {"RUNNING_MODAL"}   # click stays on the panel
        return {"PASS_THROUGH"}

    def _end(self, ctx):
        close_overlay()
        return {"CANCELLED"}


def close_overlay():
    """Remove drawing and timers immediately, including on file load/uninstall."""
    STATE.stop = True
    STATE.token += 1
    if STATE.handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(STATE.handle, "WINDOW")
        except (ValueError, ReferenceError):
            pass
    if STATE.timer is not None and STATE.wm is not None:
        try:
            STATE.wm.event_timer_remove(STATE.timer)
        except (ValueError, ReferenceError):
            pass
    STATE.handle = STATE.timer = None
    STATE.running = False
    STATE.owner_window = STATE.owner_area = 0
    STATE.hits.clear()
    STATE.hover = None
    STATE.focus=False;STATE.page='main'
    redraw()


class BWM_OT_prompt(bpy.types.Operator):
    bl_idname = "bwm.prompt"
    bl_label = "Ask BlendWMe"
    text: StringProperty(name="What to build")

    def invoke(self, ctx, ev):
        self.text = STATE.prompt
        return ctx.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, ctx):
        STATE.prompt = self.text
        STATE.send(self.text)
        return {"FINISHED"}


class BWM_PT_launcher(bpy.types.Panel):
    bl_label = "BlendWMe"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendWMe"

    def draw(self, ctx):
        self.layout.operator("bwm.overlay")
        if hasattr(ctx.scene, "bwm"):
            self.layout.prop(ctx.scene.bwm, "graphite_scale", text="Panel scale")


CLASSES = (BWM_OT_overlay, BWM_OT_prompt, BWM_PT_launcher)


def demo():
    """Shows all three indicators on the active object (or the origin)."""
    ob = bpy.context.active_object
    name = ob.name if ob else None
    clear()
    indicate("block", "Scale me", obj=name, xy=(400, 300))
    indicate("radar", "Look here", xy=(300, 220))
    indicate("circle", "Bevel", rect=(520, 260, 90, 40))


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    close_overlay()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
