"""Knowledge the AI can pull in when it needs it.

1. Skill cards (skills/*.md): tested artist workflows. The AI sees a short
   index in its instructions and loads the full card only when relevant.
   This keeps prompts small and gives the model real technique, not guesses.
2. inspect_api: live lookup of Blender's own API, so the model checks the
   real property and operator names in YOUR Blender version instead of
   inventing them.
"""

import os

import bpy

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
VALIDATED_CARDS = {"example-cottage", "example-room", "example-ice-cream-cone", "camera-help"}


def _read(name):
    path = os.path.join(SKILLS_DIR, name + ".md")
    with open(path, encoding="utf-8") as f:
        return f.read()


def skill_index():
    """[(name, one-line description)] from each card's first line."""
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for fn in sorted(os.listdir(SKILLS_DIR)):
        if not fn.endswith(".md"):
            continue
        name = fn[:-3]
        if name not in VALIDATED_CARDS:
            continue
        with open(os.path.join(SKILLS_DIR, fn), encoding="utf-8") as f:
            first = f.readline().strip().lstrip("# ").strip()
        out.append((name, first))
    return out


def load_skill(name):
    name = os.path.basename(str(name)).replace(".md", "")
    if name not in VALIDATED_CARDS:
        return ("This card is not enabled: its executable workflow needs current-version validation. "
                "Use inspect_api or a validated demo card. Broader artistic cards remain planned.")
    try:
        return ("Validation: executable fixtures on Blender 5.2.2; new changes on Blender 4.2 remain unverified.\n\n"
                + _read(name))
    except OSError:
        names = ", ".join(n for n, _ in skill_index())
        return f"No skill called {name!r}. Available: {names}"


# ----------------------------------------------------------------------
# Blender API lookup
# ----------------------------------------------------------------------

def _describe_props(rna, limit=60):
    rows = []
    for prop in rna.properties:
        if prop.identifier == "rna_type":
            continue
        row = f"{prop.identifier} ({prop.type}"
        if getattr(prop, "is_readonly", False):
            row += ", read-only"
        if prop.type == "ENUM":
            items = [i.identifier for i in prop.enum_items][:20]
            row += ", one of " + "|".join(items) if items else ""
        elif prop.type in {"INT", "FLOAT"} and getattr(prop, "array_length", 0):
            row += f", array[{prop.array_length}]"
        elif prop.type == "POINTER" and getattr(prop, "fixed_type", None):
            row += f" -> {prop.fixed_type.identifier}"
        row += ")"
        if prop.description:
            row += ": " + prop.description[:90]
        rows.append(row)
        if len(rows) >= limit:
            rows.append("...")
            break
    return rows


# ----------------------------------------------------------------------
# bmesh and mathutils are plain Python modules (no RNA), so we read them with
# Python's own introspection: members, docstrings and bmesh.ops arguments.
# ----------------------------------------------------------------------
_MATHUTILS_CLASSES = ("Vector", "Matrix", "Euler", "Quaternion", "Color")


def _python_root(path):
    """The module/class a path starts from, and the remaining parts. None if not ours."""
    import bmesh
    import mathutils
    parts = [p for p in path.split(".") if p]
    if not parts:
        return None
    head = parts[0]
    if head == "bmesh":
        return bmesh, "bmesh", parts[1:]
    if head == "mathutils":
        return mathutils, "mathutils", parts[1:]
    if head in _MATHUTILS_CLASSES:
        return mathutils, "mathutils", parts
    if head.startswith("BM") and hasattr(bmesh.types, head):
        return bmesh.types, "bmesh.types", parts
    if head in ("BVHTree", "KDTree"):
        sub = "bvhtree" if head == "BVHTree" else "kdtree"
        return getattr(mathutils, sub), "mathutils." + sub, parts
    return None


def _first_line(obj):
    return (getattr(obj, "__doc__", "") or "").strip().split("\n")[0][:140]


def _members(obj, limit=90):
    import types
    rows = {}
    for n in dir(obj):
        if n.startswith("_"):
            continue
        try:
            value = getattr(obj, n)
        except Exception:
            continue
        if isinstance(value, types.ModuleType):
            kind = "module"
        elif isinstance(value, type):
            kind = "class"
        elif callable(value):
            kind = "function"
        else:
            kind = "attribute"
        doc = _first_line(value) if kind != "attribute" or not isinstance(value, (int, float, str)) else ""
        rows[n] = f"{kind}: {doc}" if doc else kind
        if len(rows) >= limit:
            rows["..."] = "more"
            break
    return rows


def _signature_defaults(signature):
    """[(name, default)] from 'f(bmesh, a=1, m=Matrix(), b=False)', nested brackets allowed."""
    start = signature.find("(")
    if start < 0:
        return []
    depth, items, current = 0, [], ""
    for ch in signature[start + 1:]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += ch
    items.append(current)
    out = []
    for item in items:
        name, eq, default = item.partition("=")
        if eq:
            out.append((name.strip(), default.strip()))
    return out


def _bmesh_op(path, func):
    """Signature, arguments with types and what it returns, from a bmesh.ops docstring."""
    doc = (func.__doc__ or "").strip()
    lines = [line.strip() for line in doc.splitlines()]
    args, types_, returns = {}, {}, []
    for line in lines:
        if line.startswith(":arg "):
            name, _, text = line[5:].partition(":")
            args[name.strip()] = text.strip()
        elif line.startswith(":type "):
            name, _, text = line[6:].partition(":")
            types_[name.strip()] = text.strip()
        elif line.startswith(":return:") or (returns and line.startswith("- ")):
            returns.append(line)
    arguments = {n: (f"({types_[n]}) " if n in types_ else "") + t for n, t in args.items()}
    # Blender 4.2 docstrings only have the signature: "op(bmesh, cap_ends=False, ..., matrix=Matrix())".
    for name, default in _signature_defaults(lines[0] if lines else ""):
        arguments.setdefault(name, f"default {default}")
    if not returns and lines and "->" in lines[0]:
        returns = [lines[0].split("->", 1)[1].strip()]
    summary = next((line for line in lines[1:] if line and not line.startswith((":", "->"))), "")
    return {"bmesh_op": path, "signature": lines[0] if lines else path, "summary": summary,
            "arguments": arguments, "returns": " ".join(returns)[:600],
            "example": f"result = {path}(bm, ...)  # always pass the BMesh first; keywords for the rest",
            "tip": "bwm has ready-made shapes (bwm.cylinder, bwm.cone, bwm.loft, bwm.extrude_profile...) "
                   "that avoid most bmesh code."}


def _inspect_python(path):
    """inspect_api for bmesh, bmesh.ops.X, bmesh.types.*, mathutils.* (None if the path is not one)."""
    import difflib
    root = _python_root(path)
    if root is None:
        return None
    obj, label, rest = root
    for part in rest:
        if not hasattr(obj, part):
            options = [n for n in dir(obj) if not n.startswith("_")]
            close = difflib.get_close_matches(part, options, n=5, cutoff=0.5)
            return {"error": f"{label}.{part} does not exist.", "did_you_mean": close or options[:40]}
        obj = getattr(obj, part)
        label = f"{label}.{part}"
    import types
    if label.startswith("bmesh.ops.") and callable(obj):
        return _bmesh_op(label, obj)
    if isinstance(obj, types.ModuleType) or label == "bmesh.ops":
        out = {"module": label, "doc": _first_line(obj), "members": _members(obj, 160 if label == "bmesh.ops" else 90)}
        if label == "bmesh":
            out["usage"] = ("bm = bmesh.new(); bm.from_mesh(me) or build with bm.verts.new((x, y, z)) and "
                            "bm.faces.new((v1, v2, v3)); bmesh.ops.<op>(bm, ...); bm.to_mesh(me); bm.free(). "
                            "There is no bm.ops, bm.new or bm.to_object.")
        return out
    if isinstance(obj, type):
        return {"class": label, "doc": (obj.__doc__ or "").strip()[:600], "members": _members(obj)}
    if callable(obj):
        return {"function": label, "doc": (obj.__doc__ or "").strip()[:1500]}
    return {"value": label, "type": type(obj).__name__, "repr": repr(obj)[:300]}


def inspect_api(path):
    """Describe a bpy type or operator, e.g. 'BevelModifier' or 'bpy.ops.mesh.bevel'."""
    path = (path or "").strip().rstrip("()")
    try:
        if path == "bwm" or path.startswith("bwm."):
            import inspect
            from . import helpers
            public = helpers.public_names()
            names = [path[4:]] if path.startswith("bwm.") else public
            out = {}
            for n in names:
                fn = getattr(helpers, n, None)
                if fn:
                    doc = (fn.__doc__ or "").strip()
                    # One line each in the overview; the full docstring for a single function.
                    out["bwm." + n + str(inspect.signature(fn))] = doc if path.startswith("bwm.") else doc.split("\n")[0]
            return {"bwm_helpers": out or f"No bwm.{path[4:]}",
                    "note": "Use these inside the code of run_python, not as tools."}
        python_api = _inspect_python(path)
        if python_api is not None:
            return python_api
        if path.startswith("bpy.ops.") and path.count(".") == 2:
            group = path.split(".")[2]
            ops = [n for n in dir(getattr(bpy.ops, group)) if not n.startswith("_")]
            return {"operator_group": path, "operators": ops[:120]}
        if path.startswith("bpy.ops."):
            _, _, group, name = path.split(".", 3)
            op = getattr(getattr(bpy.ops, group), name)
            rna = op.get_rna_type()
            return {"operator": path, "description": rna.description,
                    "arguments": _describe_props(rna)}
        name = path.split(".")[-1]
        cls = getattr(bpy.types, name, None)
        if cls is None:
            hits = [n for n in dir(bpy.types) if name.lower() in n.lower()][:25]
            return {"error": f"No bpy.types.{name}", "did_you_mean": hits}
        rna = cls.bl_rna
        funcs = [f.identifier for f in rna.functions][:40]
        return {"type": name, "description": rna.description,
                "properties": _describe_props(rna), "methods": funcs}
    except Exception as e:
        return {"error": f"Could not inspect {path!r}: {e}. "
                         "Use 'bpy.ops.group.name' or a bpy.types class name."}
