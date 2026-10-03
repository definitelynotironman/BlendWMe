# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Explicit supported inverses plus a full-file checkpoint for other edits.

This is not a universal Blender transaction. Native operators remain interruptible
only between calls. A checkpoint is kept when an unsupported edit fails.
"""
import os
import tempfile
import uuid

import bpy

__author__ = "definitelynotironman"


class Checkpoint:
    """A recovery point taken before one run_python step.

    It has two layers:

    * a full saved copy of the .blend (``path``), verified by its header, which
      is the honest recovery route for anything the inverse layer cannot undo;
    * an in-memory inverse for the edits BlendWMe can restore exactly: object
      transforms, data assignments, material slots, existing material socket
      defaults and diffuse colours.

    Deleted datablocks, topology, animation, custom data and node-graph
    rewrites are NOT restored by ``restore``; they need the saved copy.
    """

    FOLDER = os.path.join(tempfile.gettempdir(), "blendwme-recovery")

    def __init__(self, path, objects, materials):
        """Hold the checkpoint file path and the captured inverse data."""
        self.path = path
        self.objects = objects
        self.materials = materials

    # Tuple-style access keeps older callers (``snapshot[0]``) working.
    def __getitem__(self, index):
        """Return (path, objects, materials)[index]."""
        return (self.path, self.objects, self.materials)[index]

    @classmethod
    def capture(cls):
        """Save and verify a full copy of the open file, then record the inverse data.

        Raises RuntimeError when the copy cannot be written or verified; the
        caller must not run the step in that case.
        """
        os.makedirs(cls.FOLDER, exist_ok=True)
        path = os.path.join(cls.FOLDER, str(uuid.uuid4()) + ".blend")
        # A library write retains scene datablocks but opens into an empty scene.
        # Save a copy so File > Open is a usable recovery route, while the user's
        # current filename and unsaved work remain in place. Remap relative assets
        # into the checkpoint's directory so references still resolve on recovery.
        saved = bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=False,
                                            relative_remap=True, check_existing=False)
        if "FINISHED" not in saved:
            raise RuntimeError("The recovery checkpoint could not be saved; this step was not run.")
        with open(path, "rb") as stream:
            if stream.read(7) != b"BLENDER":
                raise RuntimeError("The recovery checkpoint could not be verified; this step was not run.")
        return cls(path, cls._object_state(), cls._material_state())

    @staticmethod
    def _object_state():
        """[(object, world matrix, data, material list or None)] for every object."""
        return [(o, o.matrix_world.copy(), o.data,
                 list(o.data.materials) if hasattr(o.data, "materials") else None)
                for o in bpy.data.objects]

    @staticmethod
    def _material_state():
        """[(material, diffuse colour, [(node name, input index, value)])] for every material."""
        materials = []
        for mat in bpy.data.materials:
            sockets = []
            if mat.node_tree:
                for node in mat.node_tree.nodes:
                    for index, socket in enumerate(node.inputs):
                        if hasattr(socket, "default_value"):
                            value = socket.default_value
                            sockets.append((node.name, index, tuple(value) if hasattr(value, "__len__") else value))
            materials.append((mat, tuple(mat.diffuse_color), sockets))
        return materials

    def restore(self):
        """Restore transforms, data assignments, material slots and existing socket values.

        Deleted objects/nodes, topology, animation, custom properties and node graph
        rewrites need the checkpoint; never report those as automatically restored.
        Returns the checkpoint path so the caller can tell the user where it is.
        """
        for obj, matrix, data, slots in self.objects:
            try:
                obj.data = data
                obj.matrix_world = matrix
                if slots is not None:
                    obj.data.materials.clear()
                    for mat in slots:
                        obj.data.materials.append(mat)
            except (ReferenceError, RuntimeError, TypeError):
                pass
        for mat, color, sockets in self.materials:
            try:
                mat.diffuse_color = color
                for name, index, value in sockets:
                    node = mat.node_tree.nodes.get(name) if mat.node_tree else None
                    if node and index < len(node.inputs):
                        node.inputs[index].default_value = value
            except (ReferenceError, RuntimeError, TypeError, ValueError):
                pass
        return self.path

    def discard(self):
        """Delete the checkpoint file after a successful step."""
        try:
            os.remove(self.path)
        except OSError:
            pass


def capture():
    """Take a Checkpoint before a step (executor.py calls this)."""
    return Checkpoint.capture()


def restore(snapshot):
    """Restore what a Checkpoint supports and return its file path."""
    return snapshot.restore()


def discard(snapshot):
    """Remove a Checkpoint's file once the step succeeded."""
    snapshot.discard()
