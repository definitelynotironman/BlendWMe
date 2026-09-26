# Modeling fundamentals: scale, blockout, clean editable forms (read first for any modeling)

## Workflow
1. Blockout: rough primitives for the big masses only. Check proportions with look(mode="angles") before any detail.
2. Refine: shape each mass (loop cuts, extrude, inset, proportional edits, curves for flowing shapes).
3. Detail: secondary then tertiary shapes. Big -> medium -> small. Never detail a wrong silhouette.
4. Clean up: names, collections, parenting, smooth/flat shading, bevels.

## Scale and placement
- 1 Blender unit = 1 metre. Door 2.0-2.1 m tall, 0.9 m wide. Table top 0.75 m. Chair seat 0.45 m. Adult 1.7-1.8 m. Room ceiling 2.5-2.8 m.
- Primitive size: bpy.ops.mesh.primitive_cube_add(size=1) makes a 1 m cube centred on its origin. Set object.scale or use dimensions = (x, y, z).
- After scaling a mesh that gets modifiers (bevel, solidify), apply scale (bwm.apply_transforms(obj)) so modifier widths are even.
- Put things on the floor with bwm.ground(obj). Stack parts by reading world bounds (get_object_info) instead of guessing.

## Editable construction
- Mirror modifier for symmetric things (characters, vehicles, furniture). Model one half on +X.
- Bevel modifier (limit method ANGLE, width 0.005-0.03 m, segments 2-3) on hard edges catches light and reads as finished.
- Solidify for thin shells (walls with thickness, cloth, leaves). Array for repeats (planks, fence posts, roof tiles, steps).
- Subdivision Surface (levels 1-2 viewport) for soft organic shapes. Add support loops or a bevel to keep edges crisp.

## Code patterns
```python
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
body = C.active_object; body.name = "Table_Top"; body.scale = (1.2, 0.7, 0.04)
bwm.apply_transforms(body)
m = body.modifiers.new("Bevel", "BEVEL"); m.width = 0.01; m.segments = 2; m.limit_method = 'ANGLE'
```
Edit mode operators (good for Watch mode because a person does the same):
```python
bwm.select(obj); bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.inset(thickness=0.05)            # I
bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, 0.2)})   # E
bpy.ops.mesh.loopcut_slide(MESH_OT_loopcut={"number_cuts": 2, "edge_index": 4})   # Ctrl+R
bpy.ops.object.mode_set(mode='OBJECT')
```
Precise custom shapes: use bmesh (bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1); ... bm.to_mesh(me)).

## Common mistakes
- Floating or sinking parts. Always check bounds_min z against the support below it.
- Coplanar overlapping faces (z-fighting flicker). Offset by a few millimetres or make one part slightly bigger.
- Everything the same thickness. Vary thickness and size for visual interest.
- Leaving default names like Cube.003. Name every object.
