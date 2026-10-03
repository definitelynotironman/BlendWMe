"""Explicit supported inverses plus a full-file checkpoint for other edits.

This is not a universal Blender transaction. Native operators remain interruptible
only between calls. A checkpoint is kept when an unsupported edit fails.
"""
import os
import tempfile
import uuid

import bpy


def capture():
    folder = os.path.join(tempfile.gettempdir(), "blendwme-recovery")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, str(uuid.uuid4()) + ".blend")
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
    objects = [(o, o.matrix_world.copy(), o.data, list(o.data.materials) if hasattr(o.data, "materials") else None)
               for o in bpy.data.objects]
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
    return path, objects, materials


def restore(snapshot):
    """Restore transforms, data assignments, material slots and existing socket values.

    Deleted objects/nodes, topology, animation, custom properties and node graph
    rewrites need the checkpoint; never report those as automatically restored.
    """
    path, objects, materials = snapshot
    for obj, matrix, data, slots in objects:
        try:
            obj.data = data
            obj.matrix_world = matrix
            if slots is not None:
                obj.data.materials.clear()
                for mat in slots:
                    obj.data.materials.append(mat)
        except (ReferenceError, RuntimeError, TypeError):
            pass
    for mat, color, sockets in materials:
        try:
            mat.diffuse_color = color
            for name, index, value in sockets:
                node = mat.node_tree.nodes.get(name) if mat.node_tree else None
                if node and index < len(node.inputs):
                    node.inputs[index].default_value = value
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            pass
    return path


def discard(snapshot):
    try:
        os.remove(snapshot[0])
    except OSError:
        pass
