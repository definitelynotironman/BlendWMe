"""Catch made-up Blender API names BEFORE the code runs.

Open models often invent names that sound right: `bpy.active_object`,
`bm.new(...)`, `bpy.ops.mesh.add_cube()`. Running that code wastes a model
call and can leave a half-built step. This check reads the code (without
running it), looks up every bpy / bmesh / math / bwm name in the real API
of this Blender, and suggests the right one.

It only flags names it can prove are wrong, so valid code is never blocked.
"""

import ast
import difflib
import inspect
import math
import re

import bmesh
import bpy
import mathutils

from . import helpers

# Things people write on `bpy` that actually live on `bpy.context`.
_CONTEXT_NAMES = {"active_object", "object", "selected_objects", "scene", "view_layer", "collection",
                  "mode", "active_bone", "area", "region", "window", "space_data", "edit_object"}

_BMESH_SEQ = {"verts": bmesh.types.BMVertSeq, "edges": bmesh.types.BMEdgeSeq,
              "faces": bmesh.types.BMFaceSeq}

_MODULES = {"bmesh": bmesh, "math": math, "mathutils": mathutils, "bwm": helpers}


# Old (Blender 2.7x / 3.x) API that models still write, with the 4.2 replacement.
_LEGACY = [
    (r"scene\.objects\.link\(", "`scene.objects.link()` was removed in 2.8. Use `C.scene.collection.objects.link(obj)` "
                                "or `bwm.collection(name).objects.link(obj)`."),
    (r"\.select\s*=\s*(True|False)\b", "`obj.select = True` was removed. Use `obj.select_set(True)`."),
    (r"scene\.objects\.active\b", "`scene.objects.active` was removed. Use `C.view_layer.objects.active`."),
    (r"scene\.update\(\)", "`scene.update()` was removed. Use `C.view_layer.update()`."),
    (r"\.draw_type\b", "`draw_type` is now `display_type`."),
    (r"\.show_x_ray\b", "`show_x_ray` is now `show_in_front`."),
    (r"\.uv_textures\b", "`uv_textures` is now `uv_layers`."),
    (r"material_slots\.(link|append|new|add)\(", "Material slots cannot be added that way. Use "
                                                  "`obj.data.materials.append(mat)` or `bwm.assign(obj, mat)`."),
    (r"[\"']BLENDER_EEVEE[\"']", "In Blender 4.2 the EEVEE engine id is 'BLENDER_EEVEE_NEXT'."),
    (r"inputs\[\s*[\"']Subsurface[\"']\s*\]", "Principled input 'Subsurface' is now 'Subsurface Weight'."),
    (r"inputs\[\s*[\"']Specular[\"']\s*\]", "Principled input 'Specular' is now 'Specular IOR Level'."),
    (r"inputs\[\s*[\"']Emission[\"']\s*\]", "Principled input 'Emission' is now 'Emission Color'."),
    (r"inputs\[\s*[\"']Transmission[\"']\s*\]", "Principled input 'Transmission' is now 'Transmission Weight'."),
    (r"inputs\[\s*[\"']Clearcoat[\"']\s*\]", "Principled input 'Clearcoat' is now 'Coat Weight'."),
    (r"inputs\[\s*[\"']Sheen[\"']\s*\]", "Principled input 'Sheen' is now 'Sheen Weight'."),
    (r"diffuse_color\s*=\s*\(?[^)\n]*\[\s*:\s*3\s*\]", "`material.diffuse_color` needs 4 values (RGBA), not 3."),
]


def _legacy(code):
    out = []
    lines = code.splitlines()
    for pattern, message in _LEGACY:
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line) and not line.lstrip().startswith("#"):
                out.append(f"line {i}: {message}")
                break
    return out


def _bmesh_op_args(func):
    """Argument names from the op's signature line, e.g. 'create_cone(bmesh, cap_ends=False, ...)'."""
    doc = getattr(func, "__doc__", "") or ""
    first = doc.split("\n", 1)[0]
    match = re.search(r"\((.*)\)", first)
    names = set(re.findall(r"(\w+)\s*=", match.group(1))) if match else set()
    names |= set(re.findall(r":arg (\w+):", doc))
    return names


def _enum_items(rna_type, prop):
    try:
        return [i.identifier for i in rna_type.bl_rna.properties[prop].enum_items]
    except Exception:
        return []


def _check_type_strings(call):
    """modifiers.new(name, type) with a wrong or swapped type, nodes.new('NotANode')."""
    parts = _chain(call.func)
    if not parts or len(parts) < 2:
        return None
    def arg(pos, key):
        for k in call.keywords:
            if k.arg == key and isinstance(k.value, ast.Constant):
                return k.value.value
        if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
            return call.args[pos].value
        return None
    if parts[-2:] == ["modifiers", "new"]:
        kind = arg(1, "type")
        valid = _enum_items(bpy.types.Modifier, "type")
        if isinstance(kind, str) and valid and kind not in valid:
            name = arg(0, "name")
            if isinstance(name, str) and name in valid:
                return f"`modifiers.new({name!r}, {kind!r})` has name and type swapped. Use modifiers.new({kind!r}, {name!r})."
            return f"Modifier type {kind!r} does not exist." + _close(kind.upper(), valid)
    if parts[-2:] == ["nodes", "new"]:
        kind = arg(0, "type")
        if isinstance(kind, str) and kind and not hasattr(bpy.types, kind):
            return f"Node type {kind!r} does not exist." + _close(kind, [n for n in dir(bpy.types) if "Node" in n])
    return None


def _check_keywords(call):
    """Unknown keyword arguments to operators, bmesh ops and bwm helpers."""
    parts = _chain(call.func)
    names = [k.arg for k in call.keywords if k.arg]
    if not parts or not names:
        return None
    valid, label = None, ".".join(parts)
    try:
        if parts[:2] == ["bpy", "ops"] and len(parts) == 4:
            rna = getattr(getattr(bpy.ops, parts[2]), parts[3]).get_rna_type()
            valid = {p.identifier for p in rna.properties if p.identifier != "rna_type"}
        elif parts[:2] == ["bmesh", "ops"] and len(parts) == 3:
            valid = _bmesh_op_args(getattr(bmesh.ops, parts[2]))
        elif parts[0] == "bwm" and len(parts) == 2:
            valid = set(inspect.signature(getattr(helpers, parts[1])).parameters)
    except Exception:
        return None
    if not valid:
        return None
    wrong = [n for n in names if n not in valid]
    if not wrong:
        return None
    return (f"`{label}` has no argument {', '.join(repr(w) for w in wrong)}. "
            f"Valid: {', '.join(sorted(valid)[:14])}.")


def _chain(node):
    """['bpy', 'ops', 'mesh', 'x'] for bpy.ops.mesh.x, or None if it does not start at a name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return parts[::-1]
    return None


def _close(word, options):
    options = [o for o in options if not o.startswith("_")]
    hits = difflib.get_close_matches(word, options, n=3, cutoff=0.6)
    if not hits:   # fall back to keywords: "add_cube" finds "primitive_cube_add"
        keys = [k for k in word.lower().split("_") if len(k) >= 3 and k not in ("add", "new", "get", "set")]
        hits = [o for o in options if keys and all(k in o.lower() for k in keys)][:3]
    return (" Did you mean: " + ", ".join(hits) + "?") if hits else ""


def _public(obj):
    return [n for n in dir(obj) if not n.startswith("_")]


def _check_bpy(parts):
    if len(parts) < 2:
        return None
    first = parts[1]
    if not hasattr(bpy, first):
        if first in _CONTEXT_NAMES:
            return f"`bpy.{first}` does not exist. Use `bpy.context.{first}` (or `C.{first}`)."
        return f"`bpy.{first}` does not exist." + _close(first, _public(bpy))
    if first == "ops" and len(parts) >= 4:
        group, name = parts[2], parts[3]
        if group not in _public(bpy.ops):
            return f"`bpy.ops.{group}` is not an operator group." + _close(group, _public(bpy.ops))
        sub = getattr(bpy.ops, group)
        if name in ("get_rna_type", "idname", "poll"):
            return None
        if name not in _public(sub):
            return f"`bpy.ops.{group}.{name}` does not exist." + _close(name, _public(sub))
    if first == "types" and len(parts) >= 3 and not hasattr(bpy.types, parts[2]):
        return f"`bpy.types.{parts[2]}` does not exist." + _close(parts[2], _public(bpy.types))
    if first == "data" and len(parts) >= 3 and not hasattr(bpy.data, parts[2]):
        return f"`bpy.data.{parts[2]}` does not exist." + _close(parts[2], _public(bpy.data))
    return None


def _check_module(parts):
    root, module = parts[0], _MODULES[parts[0]]
    obj = module
    for i, part in enumerate(parts[1:], 1):
        if not hasattr(obj, part):
            path = ".".join(parts[:i + 1])
            hint = ""
            if root == "bwm":
                hint = " bwm has: " + ", ".join(n for n in _public(helpers)
                                                if callable(getattr(helpers, n)) and n[0].islower()
                                                and n not in ("contextlib", "math", "bpy"))
            return f"`{path}` does not exist." + (_close(part, _public(obj)) or hint)
        obj = getattr(obj, part)
        if not isinstance(obj, type(math)) and not isinstance(obj, type):
            break   # stop at functions/values; we cannot see further safely
    return None


def _check_bm(parts):
    name, attr = parts[0], parts[1]
    if attr == "new":
        return (f"`{name}.new()` does not exist on a BMesh. Use `{name}.verts.new(co)`, "
                f"`{name}.edges.new((v1, v2))` or `{name}.faces.new((v1, v2, v3, ...))`.")
    if not hasattr(bmesh.types.BMesh, attr):
        return f"`{name}.{attr}` does not exist on a BMesh." + _close(attr, _public(bmesh.types.BMesh))
    if attr in _BMESH_SEQ and len(parts) >= 3 and not hasattr(_BMESH_SEQ[attr], parts[2]):
        return (f"`{name}.{attr}.{parts[2]}` does not exist."
                + _close(parts[2], _public(_BMESH_SEQ[attr])))
    return None


def check(code):
    """Return a list of problems (empty when the code looks fine)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []   # the compiler reports syntax errors with a line number
    # Names the code assigns itself (we never second-guess those),
    # and variables that hold a BMesh (from bmesh.new / from_edit_mesh).
    assigned = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    assigned |= {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assigned |= {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
    bm_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if _chain(node.value.func) in (["bmesh", "new"], ["bmesh", "from_edit_mesh"]):
                bm_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    problems, seen = _legacy(code), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            issue = _check_keywords(node) or _check_type_strings(node)
            if issue:
                text = f"line {node.lineno}: {issue}"
                if text not in problems:
                    problems.append(text)
            continue
        if not isinstance(node, ast.Attribute):
            continue
        parts = _chain(node)
        if not parts or tuple(parts) in seen:
            continue
        seen.add(tuple(parts))
        root = parts[0]
        issue = None
        if root == "bpy" and root not in assigned:
            issue = _check_bpy(parts)
        elif root in _MODULES and root not in assigned:
            issue = _check_module(parts)
        elif root in bm_names:
            issue = _check_bm(parts)
        if issue and issue not in problems:
            problems.append(f"line {node.lineno}: {issue}")
    return problems[:6]
