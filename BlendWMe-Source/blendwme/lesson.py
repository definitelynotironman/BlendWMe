# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Typed, read-only lesson checks. No generated Python is evaluated.

A Teach step can ask BlendWMe to recognise when the learner has done the
action ("select the cube", "enter Edit Mode"). The brain describes the
expected state as a small typed fact; this module validates it once on
Blender's main thread and later compares the live scene with it. Polling
never changes mode, selection or data.
"""
import math
import uuid

import bpy
from . import bridge

__author__ = "definitelynotironman"

KINDS = {"selected", "active", "mode", "modifier", "property"}
PROPERTIES = {"location", "scale", "rotation_euler", "dimensions"}


class LessonCheck:
    """One expected learner outcome, bound to the context where the lesson began.

    The check remembers the target object's stable BlendWMe id (so a rename does
    not break it), the scene, view layer, window and editor, and the bridge epoch
    (so a New lesson or file load makes old checks report "unavailable" instead
    of silently advancing a different lesson).
    """

    def __init__(self, spec, object_id, scene, layer, epoch, area, editor, window):
        """Store an already validated spec and its originating context pointers."""
        self.spec = spec
        self.object_id = object_id
        self.scene = scene
        self.layer = layer
        self.epoch = epoch
        self.area = area
        self.editor = editor
        self.window = window

    # -- building a check ---------------------------------------------------
    @classmethod
    def prepare(cls, spec):
        """Validate a typed spec against the current context and return a LessonCheck.

        Raises ValueError with a message the brain can act on when the spec is a
        Python expression, names an unknown kind/property/mode/modifier, has
        non-finite values or names a missing object.
        """
        if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
            raise ValueError("Use a typed check: selected, active, mode, modifier or property; "
                             "Python expressions are unsupported.")
        context = bpy.context
        spec = dict(spec)
        if spec["kind"] == "property":
            spec.update(cls._property_values(spec))
        if spec["kind"] == "modifier" and spec.get("type") not in cls._enum_keys(bpy.types.Modifier, "type"):
            raise ValueError("Choose a supported modifier type from Blender's API.")
        if spec["kind"] == "mode" and spec.get("value") not in cls._enum_keys(bpy.types.Context, "mode"):
            raise ValueError("Choose a supported context mode from Blender's API.")
        obj = context.scene.objects.get(str(spec.get("object", "")))
        if obj is None and spec["kind"] != "mode":
            raise ValueError("The lesson target is missing. Read scene info and choose the actual object.")
        if obj and not obj.get("blendwme_id"):
            obj["blendwme_id"] = str(uuid.uuid4())
        return cls(spec=dict(spec),
                   object_id=obj.get("blendwme_id") if obj else None,
                   scene=context.scene.as_pointer(),
                   layer=context.view_layer.name,
                   epoch=bridge.epoch(),
                   area=context.area.as_pointer() if context.area else None,
                   editor=context.area.type if context.area else None,
                   window=context.window.as_pointer() if context.window else None)

    @staticmethod
    def _enum_keys(rna_type, prop):
        """The identifiers Blender accepts for an enum property of an RNA type."""
        return rna_type.bl_rna.properties[prop].enum_items.keys()

    @staticmethod
    def _property_values(spec):
        """Validate a property check: a known transform, three finite values, tolerance 0..1."""
        if spec.get("property") not in PROPERTIES:
            raise ValueError("Use a numeric transform property: location, scale, rotation_euler or dimensions.")
        try:
            values = tuple(float(value) for value in spec["value"])
            tolerance = float(spec.get("tolerance", 0.001))
            finite = all(math.isfinite(value) for value in values) and math.isfinite(tolerance)
            if len(values) != 3 or not finite or not 0 <= tolerance <= 1:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("Property checks need three finite values and a tolerance from 0 to 1.") from None
        return {"value": values, "tolerance": tolerance}

    # -- evaluating a check -------------------------------------------------
    def evaluate(self):
        """Return (state, feedback). Polling never changes mode, selection or data.

        state is "correct", "incorrect" or "unavailable" (the lesson's context is
        gone, so the learner needs a recovery route rather than a silent False).
        """
        context = bpy.context
        problem = self._context_problem(context)
        if problem:
            return "unavailable", problem
        kind = self.spec["kind"]
        obj = next((o for o in context.scene.objects if o.get("blendwme_id") == self.object_id), None)
        if kind != "mode" and (obj is None or obj.name not in context.view_layer.objects):
            return "unavailable", "The lesson object is no longer available in this view layer. Start a new lesson."
        if kind == "property":
            correct = self._property_matches(obj)
            if correct is None:
                return "unavailable", "The expected value is invalid; ask for a corrected lesson step."
        else:
            correct = self._fact_matches(kind, obj, context)
        if correct:
            return "correct", "Step complete."
        return "incorrect", "Follow the instruction on the named lesson object, then check again."

    def _context_problem(self, context):
        """A recovery message when the lesson's epoch, scene, window or editor is gone, else ""."""
        if self.epoch != bridge.epoch():
            return "This lesson belongs to earlier work. Start a new lesson."
        if context.scene.as_pointer() != self.scene or context.view_layer.name != self.layer:
            return "Return to the lesson's scene and view layer, or start a new lesson."
        if self.window and (not context.window or context.window.as_pointer() != self.window):
            return "Return to the Blender window where this lesson began."
        if self.area and context.window:
            area = next((a for a in context.window.screen.areas if a.as_pointer() == self.area), None)
            if area is None or area.type != self.editor:
                return "Restore the editor where this lesson began, or start a new lesson."
        return ""

    def _fact_matches(self, kind, obj, context):
        """True when a selected / active / mode / modifier fact holds right now."""
        if kind == "selected":
            return obj.select_get(view_layer=context.view_layer)
        if kind == "active":
            return context.view_layer.objects.active == obj
        if kind == "mode":
            return (context.mode == self.spec.get("value")
                    and (self.object_id is None or context.view_layer.objects.active == obj))
        return any(m.type == self.spec.get("type") for m in obj.modifiers)

    def _property_matches(self, obj):
        """True/False for a transform within tolerance, or None when the spec is unusable."""
        name = self.spec.get("property")
        if name not in PROPERTIES:
            return None
        try:
            expected = tuple(float(v) for v in self.spec["value"])
            actual = tuple(getattr(obj, name))
            tolerance = max(0, min(1, float(self.spec.get("tolerance", 0.001))))
        except (KeyError, TypeError, ValueError):
            return None
        return len(expected) == len(actual) and all(abs(a - b) <= tolerance for a, b in zip(actual, expected))


def prepare(spec):
    """Validate a typed lesson fact on the main thread (see LessonCheck.prepare)."""
    return LessonCheck.prepare(spec)


def evaluate(check):
    """Compare the live scene with a prepared LessonCheck (see LessonCheck.evaluate)."""
    return check.evaluate()
