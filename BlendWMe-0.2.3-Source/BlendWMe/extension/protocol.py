"""Strict, Blender-independent boundary. No generated Python is ever executed."""
import json
import math

PRIMITIVES = ("cube", "cylinder", "cone", "uv_sphere", "plane", "torus")
SCHEMA = {
    "create_primitive": {"primitive": "cube|cylinder|cone|uv_sphere|plane|torus", "name": "unique string", "location": "optional vec3", "rotation_deg": "optional vec3", "scale": "optional positive vec3"},
    "set_transform": {"name": "existing object", "location": "optional vec3", "rotation_deg": "optional vec3", "scale": "optional positive vec3"},
    "set_material": {"name": "existing mesh", "base_color": "RGBA in 0..1", "roughness": "optional 0..1", "metallic": "optional 0..1"},
    "add_bevel": {"name": "existing mesh", "width": "0.001..1", "segments": "integer 1..8"},
}
SYSTEM = '''You are BlendWMe, a Blender tutor and collaborative scene assistant.
Return one JSON object: {"message": "brief explanation", "done": false,
"action": {"tool": "allowed tool", "args": {}},
"guide": {"title": "short title", "instruction": "specific Blender UI/keyboard instructions with exact values", "why": "one useful fact", "target": "ADD|ITEM|OBJECT|MODIFIER|MATERIAL"}}.
action can be null. done=true means the requested work is complete; no action then.
Choose ONE small action. Never Python, scripts, code, deletion, external assets or files.
In TEACH mode propose a verifiable action for the HUMAN and explain exact UI steps.
The application WILL NOT execute that action in TEACH mode. Keep each step small.
Use only objects present in scene; create new unique names. Check history before repeating.
Respect manual edits and preserve the user's proportions and positions. A scene summary
is structured data, NOT a rendered image: do not claim to have seen pixels.
No arbitrary UI coordinates. Use only supported guide targets.
If ambiguous, action=null and explain. At most 24 actions per session.
Tools: ''' + json.dumps(SCHEMA)

def number(v, lo, hi, label):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{label} must be finite and in {lo}..{hi}")
    return float(v)

def vector(v, size, lo, hi, label):
    if not isinstance(v, (list, tuple)) or len(v) != size:
        raise ValueError(f"{label} requires {size} numbers")
    return [number(x, lo, hi, label) for x in v]

def name(v):
    if not isinstance(v, str) or not v.strip() or len(v) > 63 or any(ord(c) < 32 for c in v):
        raise ValueError("Object name must contain 1..63 printable characters")
    return v.strip()

def validate_action(action):
    if not isinstance(action, dict) or set(action) != {"tool", "args"}:
        raise ValueError("Action must contain exactly tool and args")
    tool, args = action["tool"], action["args"]
    if tool not in SCHEMA or not isinstance(args, dict) or not args:
        raise ValueError("Unknown tool or missing arguments")
    if set(args) - set(SCHEMA[tool]):
        raise ValueError("Unexpected tool arguments")
    a = dict(args)
    a["name"] = name(a.get("name"))
    if tool == "create_primitive":
        if a.get("primitive") not in PRIMITIVES:
            raise ValueError("Unsupported primitive")
    if tool in ("create_primitive", "set_transform"):
        for k, lo, hi in (("location", -1000, 1000), ("rotation_deg", -3600, 3600), ("scale", .001, 100)):
            if k in a:
                a[k] = vector(a[k], 3, lo, hi, k)
        if tool == "set_transform" and not set(a) & {"location", "rotation_deg", "scale"}:
            raise ValueError("Transform requires a changed field")
    if tool == "set_material":
        a["base_color"] = vector(a.get("base_color"), 4, 0, 1, "base_color")
        for k in ("roughness", "metallic"):
            if k in a:
                a[k] = number(a[k], 0, 1, k)
    if tool == "add_bevel":
        a["width"] = number(a.get("width", .04), .001, 1, "width")
        s = a.get("segments", 3)
        if isinstance(s, bool) or not isinstance(s, int) or not 1 <= s <= 8:
            raise ValueError("Bevel segments must be an integer in 1..8")
        a["segments"] = s
    return {"tool": tool, "args": a}

def parse_response(raw):
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("```json") and raw.endswith("```"):
            raw = raw[7:-3].strip()
        elif raw.startswith("```") and raw.endswith("```"):
            raw = raw[3:-3].strip()
        raw = json.loads(raw)
    if not isinstance(raw, dict) or set(raw) - {"message", "done", "action", "guide"}:
        raise ValueError("Unexpected response structure")
    if not isinstance(raw.get("done"), bool) or not isinstance(raw.get("message"), str):
        raise ValueError("Response needs a message and boolean done")
    action = validate_action(raw["action"]) if raw.get("action") is not None else None
    if raw["done"] and action:
        raise ValueError("A completed response cannot contain an action")
    guide = raw.get("guide") or {}
    if not isinstance(guide, dict) or set(guide) - {"title", "instruction", "why", "target"}:
        raise ValueError("Unexpected guide structure")
    clean = {}
    for k in ("title", "instruction", "why"):
        v = guide.get(k, "")
        if not isinstance(v, str):
            raise ValueError("Guide text must be a string")
        clean[k] = v[:1500]
    clean["target"] = guide.get("target", "OBJECT")
    if clean["target"] not in {"ADD", "ITEM", "OBJECT", "MODIFIER", "MATERIAL"}:
        raise ValueError("Unknown guide target")
    return {"message": raw["message"][:2000], "done": raw["done"], "action": action, "guide": clean}

def signature(scene):
    """Selection is harmless; geometry, transforms, materials and names matter."""
    objects = [{k: v for k, v in o.items() if k != "selected"} for o in scene["objects"]]
    return json.dumps(objects, sort_keys=True, separators=(",", ":"))

