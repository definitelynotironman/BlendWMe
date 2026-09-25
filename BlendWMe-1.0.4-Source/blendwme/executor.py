"""run_python: the AI's hands.

Like MCP for Blender, the AI writes normal Blender Python and we run it on
the live scene. No custom recipe language, no copy of the scene in a second
process. Safety comes from three simple things instead:

1. An undo step before every run, so Ctrl+Z (or the Undo AI Step button)
   takes it back.
2. A short blocklist for things that touch your files or close Blender.
3. Pause/Stop between every step.
"""

import contextlib
import io
import math
import random
import re
import sys
import traceback

import bmesh
import bpy
import mathutils
from mathutils import Euler, Matrix, Quaternion, Vector

from . import helpers, precheck, repair, scene_info

_BLOCKED = [
    (r"\bimport\s+(os|sys|subprocess|shutil|socket|ctypes|requests|urllib|http|importlib|builtins|pathlib|io)\b", "system/network modules"),
    (r"\bfrom\s+(os|sys|subprocess|shutil|socket|ctypes|requests|urllib|http|importlib|builtins|pathlib|io)\b", "system/network modules"),
    (r"__import__|\beval\(|\bexec\(", "dynamic code"),
    (r"\bopen\(", "file access"),
    (r"wm\.(quit_blender|read_homefile|read_factory_settings|open_mainfile|save_mainfile|save_as_mainfile)",
     "closing or replacing your file"),
    (r"preferences\.addons|extensions\.package|addon_utils", "add-on internals"),
    (r"\bcredentials\b|\.clipboard\b|sys\.modules|\bkeyring\b|getenv|bl_ext", "your API key or system secrets"),
    (r"__builtins__|__subclasses__|__globals__|__code__", "Python internals"),
]

MAX_OUTPUT = 4000


def check_code(code):
    for pattern, why in _BLOCKED:
        if re.search(pattern, code):
            return f"Blocked: this code uses {why}, which BlendWMe does not allow."
    return None


def _undo_push(message):
    window, area, region = helpers.find_view3d()
    try:
        if window:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.ed.undo_push(message=message)
        else:
            bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


@contextlib.contextmanager
def _bwm_importable():
    """Let `import bwm` and `from bwm import box` work during a step.

    bwm is already a ready-made name in every step, but models often import
    it first, out of habit. That used to fail with ModuleNotFoundError and the
    model then wasted turns "testing" whether bwm exists. We register the
    helpers module under the name bwm for the duration of the step only, so
    nothing outside BlendWMe ever sees it.
    """
    previous = sys.modules.get("bwm")
    sys.modules["bwm"] = helpers
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("bwm", None)
        else:
            sys.modules["bwm"] = previous


def _namespace():
    return {
        "__name__": "blendwme_step",
        "bpy": bpy, "bmesh": bmesh, "math": math, "random": random,
        "mathutils": mathutils, "Vector": Vector, "Matrix": Matrix,
        "Euler": Euler, "Quaternion": Quaternion,
        "C": bpy.context, "D": bpy.data,
        "bwm": helpers,
        repair.SET_NAME: repair.fit_set,   # forgiving `owner.name = value` (see repair.py)
    }


# Data types a step can create. On failure, new ones are removed again so a
# retry starts clean (no "Roof.001" duplicates from half-finished steps).
_ID_TYPES = ("objects", "collections", "meshes", "curves", "materials", "lights", "cameras",
             "armatures", "node_groups", "actions", "textures", "lattices", "metaballs",
             "grease_pencils", "fonts", "shape_keys")


def _id_pointers():
    out = {}
    for attr in _ID_TYPES:
        data = getattr(bpy.data, attr, None)
        if data is not None:
            out[attr] = {i.as_pointer() for i in data}
    return out


def _object_mode():
    obj = bpy.context.view_layer.objects.active
    if obj is not None and obj.mode != "OBJECT":
        try:
            with helpers.view3d():
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass


def _roll_back(before_ids):
    """Remove everything the failed step created. Returns how many were removed."""
    _object_mode()
    removed = 0
    for attr in _ID_TYPES:          # objects first, then their data
        data = getattr(bpy.data, attr, None)
        if data is None or attr == "shape_keys":
            continue
        new = [i for i in data if i.as_pointer() not in before_ids.get(attr, set())]
        for item in new:
            try:
                data.remove(item)
                removed += 1
            except Exception:
                pass
    return removed


# Plain-language hints for the mistakes open models make most in Blender.
_HINTS = [
    ("module 'bpy' has no attribute", "bpy itself only has bpy.data, bpy.context, bpy.ops, bpy.types, "
                                      "bpy.props, bpy.utils, bpy.path and bpy.app. The active object is "
                                      "bpy.context.active_object (or C.active_object)."),
    ("'BMesh' object has no attribute", "Use bm.verts.new(co), bm.edges.new((v1, v2)), bm.faces.new((v1, v2, v3)), "
                                        "then bm.to_mesh(mesh) and bm.free(). Or skip bmesh with "
                                        "bwm.mesh(name, verts, faces)."),
    ("NoneType' object has no attribute", "Something you expected is missing, often C.active_object after an "
                                          "operator that failed or ran without a 3D Viewport. Use the object "
                                          "returned by bwm.box / bwm.mesh, or D.objects['Name']."),
    ("poll() failed", "This operator needs a different mode, selection or active object. Set them "
                      "first (bwm.select(obj); bpy.ops.object.mode_set(...)), or use bpy.data / bmesh instead."),
    ("context is incorrect", "This operator needs a different mode, selection or active object. "
                             "Use bwm.select(obj) and the right mode, or bpy.data / bmesh instead."),
    ("'str' object has no attribute", "You passed a name where Blender needs the object itself. "
                                      "Use D.objects['Name'], D.collections['Name'] or D.materials['Name']."),
    ("bpy_prop_collection[key]", "That name does not exist. Call get_scene_info for exact names. "
                                 "Blender adds .001 to duplicate names."),
    ("not found", "Check exact names with get_scene_info or inspect_api before using them."),
    ("has no attribute", "That property or method does not exist in this Blender version. "
                         "Call inspect_api on the type to see the real names."),
    ("unexpected keyword", "Wrong argument name. Call inspect_api('bpy.ops.group.name') to see the real arguments."),
    ("enum", "Use one of the allowed values. inspect_api lists them for each property."),
    ("Mesh does not exist in edit mode", "Switch to Object Mode before reading or changing mesh data."),
]


def _hint(error):
    for needle, hint in _HINTS:
        if needle in error:
            return hint
    return ""


def _readable_error(code):
    """Traceback lines that point into the AI's own code, plus the line text."""
    tb = traceback.format_exc()
    lines = code.splitlines()
    out = []
    for frame in traceback.extract_tb(sys.exc_info()[2]):
        if frame.filename == "<blendwme>" and 0 < frame.lineno <= len(lines):
            out.append(f"line {frame.lineno}: {lines[frame.lineno - 1].strip()}")
    last = tb.strip().splitlines()[-1]
    return (" | ".join(out[-3:]) + "\n" if out else "") + last


def run(code, title="AI step", author="ai"):
    """Run code on the live scene. Returns a dict for the AI."""
    blocked = check_code(code)
    if blocked:
        return {"ok": False, "error": blocked}
    try:
        compiled = compile(code, "<blendwme>", "exec")
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError line {e.lineno}: {e.msg}. {(e.text or '').strip()}",
                "rolled_back": "Nothing ran."}
    # Repair mechanical API slips first; the pre-check only sees what is left.
    original = code
    code, repairs = repair.repair(code)
    compiled = compile(code, "<blendwme>", "exec")
    problems = precheck.check(code)
    if problems:
        result = {"ok": False, "error": "Pre-check found names that do not exist in Blender "
                                        + bpy.app.version_string + ":\n" + "\n".join(problems),
                  "rolled_back": "Nothing ran. Fix these names and send the whole step again."}
        if repairs:
            result["auto_repaired"] = repairs
        return result
    del repair.NOTES[:]
    helpers.begin_step()

    before = scene_info.snapshot()
    before_ids = _id_pointers()
    _undo_push(f"Before BlendWMe: {title}")
    stdout = io.StringIO()
    window, area, region = helpers.find_view3d()
    ok, error = True, None
    try:
        with contextlib.redirect_stdout(stdout), _bwm_importable():
            if window:
                with bpy.context.temp_override(window=window, area=area, region=region,
                                               screen=window.screen):
                    exec(compiled, _namespace())
            else:
                exec(compiled, _namespace())
    except Exception:
        ok = False
        error = _readable_error(original)   # same line numbers, the model's own text
    repairs = repairs + [n for n in repair.NOTES if n not in repairs]
    removed = 0
    if not ok:
        removed = _roll_back(before_ids)
    # Earlier AI objects this step replaced: deleted on success, restored on failure.
    helpers.end_step(ok)
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    after = scene_info.snapshot()
    lines, changed = scene_info.diff(before, after)
    scene_info.tag_author(changed["added"], author)
    _undo_push(f"BlendWMe: {title}")

    result = {"ok": ok}
    if ok:
        in_scene = {o.as_pointer() for o in bpy.context.scene.objects}
        lost = [o.name for o in bpy.data.objects
                if o.as_pointer() not in before_ids["objects"] and o.as_pointer() not in in_scene]
        if lost:
            result["warning"] = (f"{len(lost)} new object(s) are NOT in the scene, so nobody can see them: "
                                 f"{lost[:8]}. Link them with bwm.collection(name).objects.link(obj) or "
                                 "C.scene.collection.objects.link(obj).")
    if repairs:
        result["auto_repaired"] = repairs[:12]
        result["repair_note"] = ("BlendWMe fixed these small API slips and ran the step"
                                 + (" successfully" if ok else "")
                                 + ". Write it the fixed way next time.")
    if helpers.NOTES:
        result["helper_notes"] = list(helpers.NOTES)[:8]
    printed = stdout.getvalue()
    if printed:
        result["printed"] = printed[:MAX_OUTPUT] + ("..." if len(printed) > MAX_OUTPUT else "")
    if error:
        result["error"] = error
        hint = _hint(error)
        if hint:
            result["hint"] = hint
        result["rolled_back"] = (f"Removed {removed} new item(s) this step created. "
                                 "Fix the code and run the WHOLE step again."
                                 if removed else "Nothing new was kept. Fix the code and run the whole step again.")
        if lines:
            result["note"] = "These EXISTING objects were changed before the error and keep that change: " \
                             + "; ".join(lines[:10])
    result["changes"] = lines[:60] if lines else ["(no object-level changes)"]
    result["mode"] = bpy.context.mode
    result["_changed"] = changed  # used by the UI, removed before sending
    return result
