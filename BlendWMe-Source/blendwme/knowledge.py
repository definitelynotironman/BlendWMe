# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Knowledge the AI can pull in when it needs it.

1. Skill cards (skills/*.md): tested artist workflows. The AI sees a short
   index in its instructions and loads the full card only when relevant.
   This keeps prompts small and gives the model real technique, not guesses.
2. inspect_api: live lookup of Blender's own API, so the model checks the
   real property and operator names in YOUR Blender version instead of
   inventing them.
"""

import difflib
import inspect
import os
import types

import bpy

__author__ = "definitelynotironman"

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
VALIDATED_CARDS = {"example-cottage", "example-room", "example-ice-cream-cone", "camera-help"}


class SkillLibrary:
    """The technique cards shipped in skills/.

    A card is offered only after its executable workflow has been validated in
    real Blender (VALIDATED_CARDS). Other cards stay on disk as planned work but
    are refused with an explanation, so the brain is never handed an untested
    recipe as if it were reliable.
    """

    def __init__(self, folder=SKILLS_DIR, enabled=VALIDATED_CARDS):
        """Read cards from folder; only names in enabled are offered."""
        self.folder = folder
        self.enabled = enabled

    def _read(self, name):
        """The full Markdown text of one card."""
        with open(os.path.join(self.folder, name + ".md"), encoding="utf-8") as f:
            return f.read()

    def index(self):
        """[(name, one-line description)] from each enabled card's first line."""
        out = []
        if not os.path.isdir(self.folder):
            return out
        for fn in sorted(os.listdir(self.folder)):
            name = fn[:-3]
            if not fn.endswith(".md") or name not in self.enabled:
                continue
            with open(os.path.join(self.folder, fn), encoding="utf-8") as f:
                first = f.readline().strip().lstrip("# ").strip()
            out.append((name, first))
        return out

    def load(self, name):
        """The card text with its validation note, or an explanation why it is unavailable."""
        name = os.path.basename(str(name)).replace(".md", "")
        if name not in self.enabled:
            return ("This card is not enabled: its executable workflow needs current-version validation. "
                    "Use inspect_api or a validated demo card. Broader artistic cards remain planned.")
        try:
            return ("Validation: executable fixtures on Blender 5.2.2; new changes on Blender 4.2 remain unverified.\n\n"
                    + self._read(name))
        except OSError:
            names = ", ".join(n for n, _ in self.index())
            return f"No skill called {name!r}. Available: {names}"


class PythonApiInspector:
    """inspect_api for the plain Python modules Blender ships: bmesh and mathutils.

    They have no RNA, so they are read with Python's own introspection:
    members, docstrings and the argument lists in bmesh.ops docstrings.
    """

    MATHUTILS_CLASSES = ("Vector", "Matrix", "Euler", "Quaternion", "Color")

    def root(self, path):
        """(module or class, label, remaining parts) a path starts from, or None if not ours."""
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
        if head in self.MATHUTILS_CLASSES:
            return mathutils, "mathutils", parts
        if head.startswith("BM") and hasattr(bmesh.types, head):
            return bmesh.types, "bmesh.types", parts
        if head in ("BVHTree", "KDTree"):
            sub = "bvhtree" if head == "BVHTree" else "kdtree"
            return getattr(mathutils, sub), "mathutils." + sub, parts
        return None

    @staticmethod
    def first_line(obj):
        """The first docstring line of obj, at most 140 characters."""
        return (getattr(obj, "__doc__", "") or "").strip().split("\n")[0][:140]

    def members(self, obj, limit=90):
        """{name: "kind: first doc line"} for the public members of obj."""
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
            doc = self.first_line(value) if kind != "attribute" or not isinstance(value, (int, float, str)) else ""
            rows[n] = f"{kind}: {doc}" if doc else kind
            if len(rows) >= limit:
                rows["..."] = "more"
                break
        return rows

    @staticmethod
    def signature_defaults(signature):
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

    def bmesh_op(self, path, func):
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
        for name, default in self.signature_defaults(lines[0] if lines else ""):
            arguments.setdefault(name, f"default {default}")
        if not returns and lines and "->" in lines[0]:
            returns = [lines[0].split("->", 1)[1].strip()]
        summary = next((line for line in lines[1:] if line and not line.startswith((":", "->"))), "")
        return {"bmesh_op": path, "signature": lines[0] if lines else path, "summary": summary,
                "arguments": arguments, "returns": " ".join(returns)[:600],
                "example": f"result = {path}(bm, ...)  # always pass the BMesh first; keywords for the rest",
                "tip": "bwm has ready-made shapes (bwm.cylinder, bwm.cone, bwm.loft, bwm.extrude_profile...) "
                       "that avoid most bmesh code."}

    def inspect(self, path):
        """inspect_api for bmesh, bmesh.ops.X, bmesh.types.*, mathutils.* (None if the path is not one)."""
        root = self.root(path)
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
        if label.startswith("bmesh.ops.") and callable(obj):
            return self.bmesh_op(label, obj)
        if isinstance(obj, types.ModuleType) or label == "bmesh.ops":
            out = {"module": label, "doc": self.first_line(obj),
                   "members": self.members(obj, 160 if label == "bmesh.ops" else 90)}
            if label == "bmesh":
                out["usage"] = ("bm = bmesh.new(); bm.from_mesh(me) or build with bm.verts.new((x, y, z)) and "
                                "bm.faces.new((v1, v2, v3)); bmesh.ops.<op>(bm, ...); bm.to_mesh(me); bm.free(). "
                                "There is no bm.ops, bm.new or bm.to_object.")
            return out
        if isinstance(obj, type):
            return {"class": label, "doc": (obj.__doc__ or "").strip()[:600], "members": self.members(obj)}
        if callable(obj):
            return {"function": label, "doc": (obj.__doc__ or "").strip()[:1500]}
        return {"value": label, "type": type(obj).__name__, "repr": repr(obj)[:300]}


class ApiInspector:
    """Answers inspect_api: bwm helpers, bmesh/mathutils, bpy operators and bpy types.

    Every answer comes from the running Blender, so names and enum values match
    the user's actual version instead of the model's memory.
    """

    def __init__(self):
        """Use a PythonApiInspector for the non-RNA modules."""
        self.python = PythonApiInspector()

    @staticmethod
    def describe_props(rna, limit=60):
        """One readable row per RNA property: name, type, enum items/array size/pointer, description."""
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

    @staticmethod
    def bwm(path):
        """The bwm helper overview, or one helper's full signature and docstring."""
        from . import helpers
        names = [path[4:]] if path.startswith("bwm.") else helpers.public_names()
        out = {}
        for n in names:
            fn = getattr(helpers, n, None)
            if fn:
                doc = (fn.__doc__ or "").strip()
                # One line each in the overview; the full docstring for a single function.
                out["bwm." + n + str(inspect.signature(fn))] = doc if path.startswith("bwm.") else doc.split("\n")[0]
        return {"bwm_helpers": out or f"No bwm.{path[4:]}",
                "note": "Use these inside the code of run_python, not as tools."}

    def operator(self, path):
        """An operator group listing, or one operator's description and arguments."""
        if path.count(".") == 2:
            group = path.split(".")[2]
            ops = [n for n in dir(getattr(bpy.ops, group)) if not n.startswith("_")]
            return {"operator_group": path, "operators": ops[:120]}
        _, _, group, name = path.split(".", 3)
        rna = getattr(getattr(bpy.ops, group), name).get_rna_type()
        return {"operator": path, "description": rna.description, "arguments": self.describe_props(rna)}

    def bpy_type(self, path):
        """Properties and methods of a bpy.types class, or close names when it does not exist."""
        name = path.split(".")[-1]
        cls = getattr(bpy.types, name, None)
        if cls is None:
            hits = [n for n in dir(bpy.types) if name.lower() in n.lower()][:25]
            return {"error": f"No bpy.types.{name}", "did_you_mean": hits}
        rna = cls.bl_rna
        return {"type": name, "description": rna.description,
                "properties": self.describe_props(rna), "methods": [f.identifier for f in rna.functions][:40]}

    def inspect(self, path):
        """Describe a bpy type or operator, e.g. 'BevelModifier' or 'bpy.ops.mesh.bevel'."""
        path = (path or "").strip().rstrip("()")
        try:
            if path == "bwm" or path.startswith("bwm."):
                return self.bwm(path)
            python_api = self.python.inspect(path)
            if python_api is not None:
                return python_api
            if path.startswith("bpy.ops."):
                return self.operator(path)
            return self.bpy_type(path)
        except Exception as e:
            return {"error": f"Could not inspect {path!r}: {e}. "
                             "Use 'bpy.ops.group.name' or a bpy.types class name."}


# Module-level entry points used by the tool host, prompts and tests.
skills = SkillLibrary()
inspector = ApiInspector()
skill_index = skills.index
load_skill = skills.load
inspect_api = inspector.inspect
