"""Rescue Python written as tool calls.

Some models (Nemotron 3 Ultra did this) call Python functions as if they
were tools: `bwm.mesh(...)`, `bpy.ops.mesh.primitive_cube_add(...)`. In one
run that wasted most of the conversation, once with 49 such calls in a row.

Instead of rejecting them, we translate a batch of those calls into ONE
Python step and run it, materials and collections first so names exist
before they are used. The model is told what happened so it learns to use
run_python itself.
"""

import inspect

PY_ROOTS = ("bwm.", "bpy.", "bmesh.", "mathutils.", "math.")
_FIRST = ("bwm.material", "bwm.collection", "bpy.data.materials", "bpy.data.collections")


def is_python_call(name):
    return isinstance(name, str) and name.startswith(PY_ROOTS)


def _filter_kwargs(name, args, helpers_module=None, bpy_module=None):
    """Drop arguments the function does not accept. Returns (kept, dropped)."""
    valid = None
    try:
        if name.startswith("bwm.") and helpers_module is not None:
            valid = set(inspect.signature(getattr(helpers_module, name[4:])).parameters)
        elif name.startswith("bpy.ops.") and bpy_module is not None:
            _, _, group, op = name.split(".", 3)
            rna = getattr(getattr(bpy_module.ops, group), op).get_rna_type()
            valid = {p.identifier for p in rna.properties}
    except Exception:
        valid = None
    if valid is None:
        return dict(args), []
    kept = {k: v for k, v in args.items() if k in valid}
    return kept, [k for k in args if k not in valid]


def to_code(calls, helpers_module=None, bpy_module=None):
    """calls: [(name, args_dict)] -> (python_code, notes)."""
    ordered = sorted(calls, key=lambda c: 0 if c[0].startswith(_FIRST) else 1)
    lines, notes = [], []
    for name, args in ordered:
        if not isinstance(args, dict):
            args = {}
        if set(args) == {"value"}:
            lines.append(f"{name}({args['value']!r})")
            continue
        kept, dropped = _filter_kwargs(name, args, helpers_module, bpy_module)
        if dropped:
            notes.append(f"{name}: ignored unknown arguments {dropped}")
        lines.append(f"{name}(**{kept!r})")
    return "\n".join(lines), notes
