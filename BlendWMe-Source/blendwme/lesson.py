"""Typed, read-only lesson checks. No generated Python is evaluated."""
import uuid
import math

import bpy
from . import bridge

KINDS = {"selected", "active", "mode", "modifier", "property"}
PROPERTIES = {"location", "scale", "rotation_euler", "dimensions"}


def prepare(spec):
    if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
        raise ValueError("Use a typed check: selected, active, mode, modifier or property; Python expressions are unsupported.")
    context = bpy.context
    spec = dict(spec)
    if spec["kind"] == "property":
        if spec.get("property") not in PROPERTIES:
            raise ValueError("Use a numeric transform property: location, scale, rotation_euler or dimensions.")
        try:
            values = tuple(float(value) for value in spec["value"])
            tolerance = float(spec.get("tolerance", 0.001))
            if len(values) != 3 or not all(math.isfinite(value) for value in values) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("Property checks need three finite values and a tolerance from 0 to 1.") from None
        spec.update(value=values, tolerance=tolerance)
    if spec["kind"] == "modifier" and spec.get("type") not in bpy.types.Modifier.bl_rna.properties["type"].enum_items.keys():
        raise ValueError("Choose a supported modifier type from Blender's API.")
    if spec["kind"] == "mode" and spec.get("value") not in bpy.types.Context.bl_rna.properties["mode"].enum_items.keys():
        raise ValueError("Choose a supported context mode from Blender's API.")
    obj = context.scene.objects.get(str(spec.get("object", "")))
    if obj is None and spec["kind"] != "mode":
        raise ValueError("The lesson target is missing. Read scene info and choose the actual object.")
    if obj and not obj.get("blendwme_id"):
        obj["blendwme_id"] = str(uuid.uuid4())
    return {"spec": dict(spec), "object_id": obj.get("blendwme_id") if obj else None,
            "scene": context.scene.as_pointer(), "layer": context.view_layer.name,
            "epoch": bridge.epoch(),
            "area": context.area.as_pointer() if context.area else None,
            "editor": context.area.type if context.area else None,
            "window": context.window.as_pointer() if context.window else None}


def evaluate(check):
    """Return (state, feedback). Polling never changes mode, selection or data."""
    context = bpy.context
    if check["epoch"] != bridge.epoch():
        return "unavailable", "This lesson belongs to earlier work. Start a new lesson."
    if context.scene.as_pointer() != check["scene"] or context.view_layer.name != check["layer"]:
        return "unavailable", "Return to the lesson's scene and view layer, or start a new lesson."
    if check["window"] and (not context.window or context.window.as_pointer() != check["window"]):
        return "unavailable", "Return to the Blender window where this lesson began."
    if check["area"] and context.window:
        area = next((area for area in context.window.screen.areas if area.as_pointer() == check["area"]), None)
        if area is None or area.type != check["editor"]:
            return "unavailable", "Restore the editor where this lesson began, or start a new lesson."
    spec = check["spec"]
    obj = next((o for o in context.scene.objects if o.get("blendwme_id") == check["object_id"]), None)
    kind = spec["kind"]
    if kind != "mode" and (obj is None or obj.name not in context.view_layer.objects):
        return "unavailable", "The lesson object is no longer available in this view layer. Start a new lesson."
    if kind == "selected":
        correct = obj.select_get(view_layer=context.view_layer)
    elif kind == "active":
        correct = context.view_layer.objects.active == obj
    elif kind == "mode":
        correct = context.mode == spec.get("value") and (check["object_id"] is None or context.view_layer.objects.active == obj)
    elif kind == "modifier":
        correct = any(m.type == spec.get("type") for m in obj.modifiers)
    else:
        name = spec.get("property")
        if name not in PROPERTIES:
            return "unavailable", "This property check is unsupported; ask for a supported check."
        try:
            expected = tuple(float(v) for v in spec["value"])
            actual = tuple(getattr(obj, name))
            tolerance = max(0, min(1, float(spec.get("tolerance", 0.001))))
            correct = len(expected) == len(actual) and all(abs(a - b) <= tolerance for a, b in zip(actual, expected))
        except (KeyError, TypeError, ValueError):
            return "unavailable", "The expected value is invalid; ask for a corrected lesson step."
    return ("correct", "Step complete.") if correct else ("incorrect", "Follow the instruction on the named lesson object, then check again.")
