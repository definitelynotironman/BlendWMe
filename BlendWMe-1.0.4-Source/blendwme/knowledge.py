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
        with open(os.path.join(SKILLS_DIR, fn), encoding="utf-8") as f:
            first = f.readline().strip().lstrip("# ").strip()
        out.append((name, first))
    return out


def load_skill(name):
    name = os.path.basename(str(name)).replace(".md", "")
    try:
        return _read(name)
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
