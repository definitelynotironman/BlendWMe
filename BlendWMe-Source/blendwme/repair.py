# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Repair mechanical Blender API slips instead of rejecting the step.

Models write Blender Python that is right in spirit
but wrong in small mechanical ways: a colour with 3 values where Blender wants
4, modifier arguments swapped, a renamed Principled input, a 2.7x property.
Rejecting that code costs a model call and, after a few, a "stuck" pause.

So before a step runs we repair what we safely can, run the fixed code, and
tell the model what changed. Three layers:

1. Text rules: exact renames that are always safe (scene.objects.link ->
   scene.collection.objects.link, inputs['Specular'] -> 'Specular IOR Level').
2. Call fixes, read from the syntax tree: modifiers.new with swapped or
   misspelled types, wrong operator / bmesh.ops keywords, enum casing.
3. Forgiving property sets: every `owner.name = value` line goes through
   fit_set(), which does a normal setattr and only steps in when Blender
   rejects it (3 vs 4 values, misspelled enum, removed property...).

Every repair keeps the code's line numbers, so error messages still point at
the model's own lines. Anything we cannot repair safely is left for the
pre-check and the normal error report. A meaningful argument we cannot map is
never dropped: Blender's own error then reaches the model.
"""

import ast
import difflib
import re

import bpy

__author__ = "definitelynotironman"

SET_NAME = "_bwm_set"

# ----------------------------------------------------------------------
# 1. Text rules: (pattern, replacement, note)
# ----------------------------------------------------------------------
_CONTEXT_NAMES = ("active_object", "selected_objects", "object", "scene", "view_layer",
                  "collection", "mode", "edit_object", "active_bone")

_PRINCIPLED_RENAMES = {
    "Subsurface": "Subsurface Weight",
    "Specular": "Specular IOR Level",
    "Emission": "Emission Color",
    "Transmission": "Transmission Weight",
    "Clearcoat": "Coat Weight",
    "Clearcoat Roughness": "Coat Roughness",
    "Clearcoat Normal": "Coat Normal",
    "Sheen": "Sheen Weight",
}

_TEXT_RULES = [
    (r"\bscene\.objects\.link\(", "scene.collection.objects.link(",
     "scene.objects.link() -> scene.collection.objects.link() (changed in 2.8)"),
    (r"\bscene\.objects\.unlink\(", "scene.collection.objects.unlink(",
     "scene.objects.unlink() -> scene.collection.objects.unlink()"),
    (r"\b(?:bpy\.data|D)\.objects\.link\(", "bpy.context.scene.collection.objects.link(",
     "bpy.data.objects.link() -> bpy.context.scene.collection.objects.link()"),
    (r"(?<![\w.])(?:[\w.]+\.)?scene\.objects\.active\b", "bpy.context.view_layer.objects.active",
     "scene.objects.active -> bpy.context.view_layer.objects.active"),
    (r"(?<![\w.])(?:[\w.]+\.)?scene\.update\(\)", "bpy.context.view_layer.update()",
     "scene.update() -> bpy.context.view_layer.update()"),
    (r"\bbpy\.(" + "|".join(_CONTEXT_NAMES) + r")\b", r"bpy.context.\1",
     "bpy.<name> -> bpy.context.<name>"),
    # D is bpy.data; models mix it up with bpy and bpy.context
    (r"\bD\.ops\.", "bpy.ops.", "D.ops -> bpy.ops (D is bpy.data)"),
    (r"\bD\.objects\.active\b", "bpy.context.view_layer.objects.active",
     "D.objects.active -> bpy.context.view_layer.objects.active"),
    (r"\bD\.(view_layer|scene|active_object|selected_objects)\b", r"bpy.context.\1",
     "D.<name> -> bpy.context.<name> (D is bpy.data)"),
    (r"\bD\.data\.", "D.", "D.data -> D (D is already bpy.data)"),
    (r"\.draw_type\b", ".display_type", "draw_type -> display_type"),
    (r"\.show_x_ray\b", ".show_in_front", "show_x_ray -> show_in_front"),
    (r"\.uv_textures\b", ".uv_layers", "uv_textures -> uv_layers"),
    (r"\.material_slots\.(?:link|append|new|add)\(", ".data.materials.append(",
     "material_slots.append() -> data.materials.append()"),
    (r"\.materials\.link\(", ".materials.append(", "materials.link() -> materials.append()"),
]
for _old, _new in _PRINCIPLED_RENAMES.items():
    _TEXT_RULES.append((r"inputs\[\s*([\"'])" + re.escape(_old) + r"\1\s*\]", f'inputs["{_new}"]',
                        f"Principled input '{_old}' -> '{_new}' (renamed in 4.0)"))


class TextRules:
    """Layer 1: regular-expression renames that are always safe to apply."""

    def __init__(self, rules=_TEXT_RULES):
        """Use the given (pattern, replacement, note) rules."""
        self.rules = rules

    def apply(self, code):
        """Return (code, notes) after every rule that changed something."""
        notes = []
        for pattern, replacement, note in self.rules:
            new = re.sub(pattern, replacement, code)
            if new != code:
                code = new
                notes.append(note)
        # bm.ops.x(...) -> bmesh.ops.x(...), unless bm really is the bmesh module.
        if not re.search(r"import\s+bmesh\s+as\s+bm\w*", code):
            new = re.sub(r"\b(bm\w*)\.ops\.", "bmesh.ops.", code)
            if new != code:
                code = new
                notes.append("bm.ops.<op> -> bmesh.ops.<op> (ops live on the bmesh module)")
        return code, notes


# ----------------------------------------------------------------------
# Source editing that keeps line numbers
# ----------------------------------------------------------------------
class SourceText:
    """Turns ast positions (line, UTF-8 byte column) into string offsets."""

    def __init__(self, code):
        """Index the start offset of every line of code."""
        self.code = code
        self.lines = code.splitlines(keepends=True)
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))

    def offset(self, lineno, col):
        """String offset of an ast (line, byte column) position."""
        line = self.lines[lineno - 1] if lineno - 1 < len(self.lines) else ""
        return self.starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))

    def span(self, node):
        """(start, end) string offsets of an ast node."""
        return self.offset(node.lineno, node.col_offset), self.offset(node.end_lineno, node.end_col_offset)

    def text(self, node):
        """The source text of an ast node."""
        a, b = self.span(node)
        return self.code[a:b]

    @staticmethod
    def apply(code, edits):
        """edits: [(start, end, new_text)]. Overlapping edits are skipped."""
        out, last = [], 0
        for start, end, text in sorted(edits):
            if start < last:
                continue
            out.append(code[last:start])
            out.append(text)
            last = end
        out.append(code[last:])
        return "".join(out)

    @staticmethod
    def chain(node):
        """['bpy', 'ops', 'mesh', 'x'] for a dotted name expression, or None."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return parts[::-1]
        return None

    @staticmethod
    def const_str(node):
        """The value of a string literal node, else None."""
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


# ----------------------------------------------------------------------
# Matching mistyped enum values and node types
# ----------------------------------------------------------------------
_MODIFIER_ALIASES = {
    "SUBDIVISION": "SUBSURF", "SUBDIVISION_SURFACE": "SUBSURF", "SUBDIVIDE": "SUBSURF", "SUBSURFACE": "SUBSURF",
    "SUBD": "SUBSURF", "BOOL": "BOOLEAN", "SOLIDIFY_MODIFIER": "SOLIDIFY", "DISPLACEMENT": "DISPLACE",
    "ARRAY_MODIFIER": "ARRAY", "SMOOTH_CORRECTIVE": "CORRECTIVE_SMOOTH", "WELDING": "WELD",
    "SHRINK_WRAP": "SHRINKWRAP", "SIMPLE_DEFORMER": "SIMPLE_DEFORM", "EDGESPLIT": "EDGE_SPLIT",
    "WEIGHTED_NORMALS": "WEIGHTED_NORMAL", "GEOMETRY_NODES": "NODES", "GEOMETRYNODES": "NODES",
}


class EnumMatcher:
    """Maps a model's spelling of an enum value or node type to Blender's real identifier."""

    def __init__(self):
        """The node-name table is built lazily (it scans bpy.types once)."""
        self.nodes = None

    @staticmethod
    def enum_items(rna_type, prop):
        """The identifiers of an RNA enum property, or [] when unavailable."""
        try:
            return [i.identifier for i in rna_type.bl_rna.properties[prop].enum_items]
        except Exception:
            return []

    @staticmethod
    def best_enum(value, items, aliases=None):
        """Match a mistyped enum value to a real one, or None."""
        if not isinstance(value, str) or not items:
            return None
        key = value.strip().upper().replace(" ", "_").replace("-", "_")
        if aliases and key in aliases and aliases[key] in items:
            return aliases[key]
        if key in items:
            return key
        if key + "_NEXT" in items:          # BLENDER_EEVEE -> BLENDER_EEVEE_NEXT in 4.2 to 4.4
            return key + "_NEXT"
        if key.endswith("_NEXT") and key[:-5] in items:     # and back again from 5.0
            return key[:-5]
        hits = difflib.get_close_matches(key, items, n=1, cutoff=0.75)
        return hits[0] if hits else None

    @staticmethod
    def node_aliases():
        """'principled bsdf' / 'shadernodeprincipledbsdf' -> 'ShaderNodeBsdfPrincipled'."""
        out = {}
        for name in dir(bpy.types):
            if not name.startswith(("ShaderNode", "GeometryNode", "CompositorNode", "FunctionNode", "TextureNode")):
                continue
            cls = getattr(bpy.types, name, None)
            try:
                label = cls.bl_rna.name
            except Exception:
                label = ""
            out[re.sub(r"[^a-z0-9]", "", name.lower())] = name
            if label:
                out.setdefault(re.sub(r"[^a-z0-9]", "", label.lower()), name)
        return out

    def best_node(self, kind):
        """The real node type for a misspelled one, or None when it is already valid or unknown."""
        if not isinstance(kind, str) or hasattr(bpy.types, kind):
            return None
        if self.nodes is None:
            self.nodes = self.node_aliases()
        key = re.sub(r"[^a-z0-9]", "", kind.lower())
        if key in self.nodes:
            return self.nodes[key]
        for prefix in ("shadernode", "geometrynode"):
            if prefix + key in self.nodes:
                return self.nodes[prefix + key]
        # 'ShaderNodePrincipledBsdf' -> same words in a different order
        words = sorted(re.findall(r"[A-Z][a-z]+|[a-z]+", kind.replace("BSDF", "Bsdf")))
        for real in self.nodes.values():
            if sorted(re.findall(r"[A-Z][a-z]+|[a-z]+", real)) == words:
                return real
        hits = difflib.get_close_matches(key, list(self.nodes), n=1, cutoff=0.85)
        return self.nodes[hits[0]] if hits else None


# ----------------------------------------------------------------------
# 2. Call fixes
# ----------------------------------------------------------------------
# Keyword names models use for operators and bmesh ops, with the real one.
_KEYWORD_RENAMES = {
    "height": "depth", "segments": "vertices", "rings": "ring_count", "sides": "vertices",
    "diameter": "radius", "radius_bottom": "radius1", "radius_top": "radius2",
    "bottom_radius": "radius1", "top_radius": "radius2", "diameter1": "radius1", "diameter2": "radius2",
    "subdivisions_count": "subdivisions", "loc": "location", "rot": "rotation", "position": "location",
    "cuts": "number_cuts",
}


class CallFixer:
    """Layer 2: repairs calls found in the syntax tree, keeping line numbers.

    Handles modifiers.new (missing or swapped name/type, misspelled type),
    nodes.new (misspelled node type), operator and bmesh.ops keywords (renamed,
    diameters halved into radii, close misspellings) and operator enum values.
    """

    def __init__(self, matcher):
        """Use matcher for enum and node-type spelling."""
        self.matcher = matcher

    def fix(self, code):
        """Return (code, notes) with every safe call repair applied."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code, []
        src = SourceText(code)
        edits, notes = [], []
        # Statement that holds each call, so `name=` can be fixed after it.
        holder = {}
        for stmt in ast.walk(tree):
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and stmt.lineno == stmt.end_lineno:
                holder[id(stmt.value)] = stmt
        bmesh_alias = any(isinstance(n, ast.Import) and any(a.name == "bmesh" and a.asname for a in n.names)
                          for n in ast.walk(tree))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            parts = SourceText.chain(call.func)
            if not parts:
                continue
            if parts[-2:] == ["modifiers", "new"]:
                self.modifiers_new(call, src, edits, notes)
            elif parts[-2:] == ["nodes", "new"]:
                self.nodes_new(call, src, edits, notes)
            elif parts[:2] == ["bpy", "ops"] and len(parts) == 4:
                props = self.op_properties(parts)
                if props:
                    self.keywords(call, parts, set(props), src, edits, notes, holder.get(id(call)), True)
                    self.op_enums(call, props, ".".join(parts), src, edits, notes)
            elif parts[:2] == ["bmesh", "ops"] and len(parts) == 3 and not bmesh_alias:
                valid = self.bmesh_op_args(parts)
                if valid:
                    self.keywords(call, parts, valid, src, edits, notes, None, False)
        if not edits:
            return code, []
        return SourceText.apply(code, edits), notes

    def modifiers_new(self, call, src, edits, notes):
        """Repair modifiers.new: type only, name only, swapped, or a misspelled type."""
        valid = self.matcher.enum_items(bpy.types.Modifier, "type")
        if not valid:
            return
        name_node = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "name"), None)
        type_node = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "type"), None)
        name, kind = SourceText.const_str(name_node), SourceText.const_str(type_node)
        if type_node is None and name_node is not None and len(call.args) == 1 and name is not None:
            # modifiers.new('BEVEL'): only a type was given.
            real = self.matcher.best_enum(name, valid, _MODIFIER_ALIASES)
            if real:
                a, b = src.span(name_node)
                edits.append((a, b, f"{real.title()!r}, {real!r}"))
                notes.append(f"modifiers.new({name!r}) -> modifiers.new({real.title()!r}, {real!r}) "
                             "(it needs a name and a type)")
            return
        if name_node is None and type_node is not None and kind is not None:
            # modifiers.new(type='BEVEL'): the name is required.
            real = self.matcher.best_enum(kind, valid, _MODIFIER_ALIASES) or kind
            a, b = src.span(call)
            edits.append((a, b, f"{src.text(call.func)}({real.title()!r}, {real!r})"))
            notes.append(f"modifiers.new(type={kind!r}) -> modifiers.new({real.title()!r}, {real!r}) (name is required)")
            return
        if kind is None or kind in valid:
            return
        if name is not None and name in valid:
            # modifiers.new('SOLIDIFY', 'Solidify'): the type id sits in the name slot.
            a1, b1 = src.span(name_node)
            a2, b2 = src.span(type_node)
            edits.append((a1, b1, repr(kind)))
            edits.append((a2, b2, repr(name)))
            notes.append(f"modifiers.new({name!r}, {kind!r}) had name and type swapped -> "
                         f"modifiers.new({kind!r}, {name!r})")
            return
        real = self.matcher.best_enum(kind, valid, _MODIFIER_ALIASES)
        if real:
            a, b = src.span(type_node)
            edits.append((a, b, repr(real)))
            notes.append(f"modifier type {kind!r} -> {real!r}")

    def nodes_new(self, call, src, edits, notes):
        """Repair nodes.new with a misspelled node type."""
        node = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "type"), None)
        kind = SourceText.const_str(node)
        real = self.matcher.best_node(kind)
        if real:
            a, b = src.span(node)
            edits.append((a, b, repr(real)))
            notes.append(f"nodes.new({kind!r}) -> nodes.new({real!r})")

    @staticmethod
    def op_properties(parts):
        """{name: RNA property} of the operator bpy.ops.<parts[2]>.<parts[3]>, or None."""
        try:
            rna = getattr(getattr(bpy.ops, parts[2]), parts[3]).get_rna_type()
        except Exception:
            return None
        return {p.identifier: p for p in rna.properties if p.identifier != "rna_type"}

    @staticmethod
    def bmesh_op_args(parts):
        """The keyword names a bmesh.ops function accepts (from its docstring), or None."""
        import bmesh
        try:
            doc = getattr(bmesh.ops, parts[2]).__doc__ or ""
        except Exception:
            return None
        first = doc.split("\n", 1)[0]
        match = re.search(r"\((.*)\)", first)
        names = set(re.findall(r"(\w+)\s*=", match.group(1))) if match else set()
        names |= set(re.findall(r":arg (\w+):", doc))
        return names or None

    def keywords(self, call, parts, valid, src, edits, notes, statement, is_op):
        """Rename keyword arguments the function does not have; never silently drop one."""
        label = ".".join(parts)
        used = {k.arg for k in call.keywords}
        for kw in call.keywords:
            if kw.arg is None or kw.arg in valid:
                continue
            value_text = src.text(kw.value)
            a, b = src.offset(kw.lineno, kw.col_offset), src.offset(kw.end_lineno, kw.end_col_offset)
            replacement, note = self._keyword_replacement(kw.arg, value_text, valid, used)
            if replacement is not None:
                edits.append((a, b, replacement))
                notes.append(f"{label}: {note}")
                continue
            if is_op and kw.arg == "name" and statement is not None:
                # bpy.ops.mesh.primitive_cube_add(name='Roof'): name the new object afterwards.
                edits.append(self.drop_keyword(kw, src))
                end = src.offset(statement.end_lineno, statement.end_col_offset)
                edits.append((end, end, f"; bpy.context.active_object.name = {value_text}"))
                notes.append(f"{label}: has no 'name' argument, so the new object is renamed after it is made")
            # Otherwise preserve meaningful intent: an unsupported argument fails with
            # Blender's diagnostic instead of silently changing the requested result.

    @staticmethod
    def _keyword_replacement(arg, value_text, valid, used):
        """(new keyword text, note) for one unknown keyword, or (None, None)."""
        if arg == "radius" and "radius1" in valid and "radius" not in valid:
            # create_cone(radius=r): a straight cylinder of that radius.
            return f"radius1={value_text}, radius2={value_text}", "radius -> radius1 and radius2"
        if arg == "diameter" and "radius" in valid:
            return f"radius=({value_text}) / 2", "diameter -> radius (half of it)"
        if arg in {"diameter", "diameter1", "diameter2"} and "radius1" in valid:
            target = "radius2" if arg == "diameter2" else "radius1"
            replacement = f"{target}=({value_text}) / 2"
            if arg == "diameter":
                replacement += f", radius2=({value_text}) / 2"
            return replacement, f"{arg} -> radius (half of it)"
        new_name = _KEYWORD_RENAMES.get(arg)
        if new_name and new_name in valid and new_name not in used:
            return f"{new_name}={value_text}", f"argument {arg!r} -> {new_name!r}"
        close = difflib.get_close_matches(arg, sorted(valid), n=1, cutoff=0.8)
        if close and close[0] not in used:
            return f"{close[0]}={value_text}", f"argument {arg!r} -> {close[0]!r}"
        return None, None

    @staticmethod
    def drop_keyword(kw, src):
        """Remove `kw=value` and its comma, keeping the newlines."""
        a, b = src.offset(kw.lineno, kw.col_offset), src.offset(kw.end_lineno, kw.end_col_offset)
        code = src.code
        # eat a following comma, else a preceding one
        m = re.match(r"\s*,", code[b:])
        if m and "\n" not in m.group(0):
            b += m.end()
        else:
            m = re.search(r",\s*$", code[:a])
            if m and "\n" not in m.group(0):
                a = m.start()
        return (a, b, "\n" * code[a:b].count("\n"))

    def op_enums(self, call, props, label, src, edits, notes):
        """Repair misspelled enum values passed to an operator."""
        for kw in call.keywords:
            prop = props.get(kw.arg) if kw.arg else None
            value = SourceText.const_str(kw.value)
            if prop is None or value is None or prop.type != "ENUM" or getattr(prop, "is_enum_flag", False):
                continue
            items = [i.identifier for i in prop.enum_items]
            if not items or value in items:
                continue
            aliases = _MODIFIER_ALIASES if kw.arg == "type" and "modifier" in label else None
            real = self.matcher.best_enum(value, items, aliases)
            if real:
                a, b = src.span(kw.value)
                edits.append((a, b, repr(real)))
                notes.append(f"{label}: {kw.arg}={value!r} -> {real!r}")


# ----------------------------------------------------------------------
# 3. Forgiving property sets
# ----------------------------------------------------------------------
_RENAMED_PROPS = {"draw_type": "display_type", "show_x_ray": "show_in_front",
                  "use_ssr": "use_raytracing", "use_soft_shadows": "use_shadows"}


class PropertySetter:
    """Layer 3: ``owner.name = value`` that repairs the value only when Blender rejects it.

    The executor rewrites each attribute assignment into a call of ``fit_set``
    (named ``_bwm_set`` in the step's namespace). ``notes`` collects what was
    repaired during the current step and is reported to the model.
    """

    def __init__(self, matcher):
        """Use matcher for misspelled enum values; start with no notes."""
        self.matcher = matcher
        self.notes = []

    @staticmethod
    def wrap_assignments(code):
        """owner.name = value  ->  _bwm_set(lambda: owner, 'name', value, line)"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code
        src = SourceText(code)
        edits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Attribute):
                continue
            owner = src.text(target.value)
            value = src.text(node.value)
            a, b = src.span(node)
            edits.append((a, b, f"{SET_NAME}(lambda: ({owner}), {target.attr!r}, ({value}), {node.lineno})"))
        return SourceText.apply(code, edits) if edits else code

    def note(self, line, text):
        """Record one repair for the model (once per line and text)."""
        entry = f"line {line}: {text}"
        if entry not in self.notes:
            self.notes.append(entry)

    @staticmethod
    def _property_names(owner):
        """The RNA property identifiers of an owner, or []."""
        try:
            return [p.identifier for p in owner.bl_rna.properties]
        except Exception:
            return []

    def fit_set(self, owner_fn, name, value, line):
        """setattr that repairs the value only when Blender rejects it."""
        owner = owner_fn()
        try:
            setattr(owner, name, value)
            return
        except AttributeError as e:
            if not isinstance(owner, bpy.types.bpy_struct):
                raise
            if self.missing_property(owner, name, value, line):
                return
            raise e
        except (TypeError, ValueError) as e:
            if not isinstance(owner, bpy.types.bpy_struct):
                raise
            fixed = self.fit_value(owner, name, value, str(e))
            if fixed is None:
                raise
            try:
                setattr(owner, name, fixed[0])
            except Exception:
                raise e from None
            self.note(line, fixed[1])

    def missing_property(self, owner, name, value, line):
        """Handle a property that does not exist: renamed, misspelled or removed. True if handled."""
        kind = type(owner).__name__
        if name == "select" and hasattr(owner, "select_set"):
            owner.select_set(bool(value))
            self.note(line, "obj.select = ... -> obj.select_set(...) (changed in 2.8)")
            return True
        names = self._property_names(owner)
        if name in names:
            return False   # it exists but is read-only: a real error
        new = _RENAMED_PROPS.get(name)
        if new and new in names:
            setattr(owner, new, value)
            self.note(line, f"{kind}.{name} -> {new}")
            return True
        close = difflib.get_close_matches(name, names, n=1, cutoff=0.85)
        if close:
            setattr(owner, close[0], value)
            self.note(line, f"{kind}.{name} does not exist, set {close[0]} instead")
            return True
        self.note(line, f"skipped: {kind} has no property {name!r} in Blender {bpy.app.version_string} "
                        "(removed or never existed). The rest of the step ran.")
        return True

    def fit_value(self, owner, name, value, message):
        """Return (fixed_value, note) or None."""
        m = re.search(r'enum "(.*?)" not found in \((.*)\)', message)
        if m:
            items = re.findall(r"'([^']*)'", m.group(2))
            real = self.matcher.best_enum(m.group(1), items)
            if real:
                return real, f"{name}={m.group(1)!r} -> {real!r}"
            return None
        m = re.search(r"should contain (\d+) items, not (\d+)", message)
        if m:
            want = int(m.group(1))
            try:
                seq = [v for v in value]
            except TypeError:
                return None
            if len(seq) < want:
                filler = 1.0 if want == 4 and len(seq) == 3 else (seq[-1] if seq else 0.0)
                seq = seq + [filler] * (want - len(seq))
            else:
                seq = seq[:want]
            extra = " (alpha 1.0 added)" if want == 4 and m.group(2) == "3" else ""
            return tuple(seq), f"{name} needs {want} values, got {m.group(2)}{extra}"
        # One number for a vector. Blender 4.x says "sequence expected"; 5.x gives no reason
        # at all, so the property itself decides: an array property gets the number repeated.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                prop = owner.bl_rna.properties[name]
                size = prop.array_length if prop.type in ("FLOAT", "INT", "BOOLEAN") else 0
            except Exception:
                return None
            if size:
                return (float(value),) * size, f"{name} needs {size} values, one number was repeated"
        return None


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
class Repairer:
    """Runs the three layers in order and guarantees line numbers are unchanged."""

    def __init__(self):
        """Build the layers around one shared EnumMatcher."""
        self.matcher = EnumMatcher()
        self.text_rules = TextRules()
        self.calls = CallFixer(self.matcher)
        self.properties = PropertySetter(self.matcher)

    def repair(self, code):
        """Return (code_to_run, notes). Line numbers are always kept."""
        original_lines = code.count("\n")
        fixed, notes = self.text_rules.apply(code)
        fixed, call_notes = self.calls.fix(fixed)
        notes += call_notes
        fixed = self.properties.wrap_assignments(fixed)
        if fixed.count("\n") != original_lines:
            # Should never happen; never risk wrong line numbers in error reports.
            return code, []
        try:
            compile(fixed, "<blendwme>", "exec")
        except SyntaxError:
            return code, []
        return fixed, notes


# The executor and pre-check use these module names.
repairer = Repairer()
NOTES = repairer.properties.notes   # repairs made while a step runs (filled by fit_set)
repair = repairer.repair
fit_set = repairer.properties.fit_set
best_enum = EnumMatcher.best_enum
best_node = repairer.matcher.best_node
