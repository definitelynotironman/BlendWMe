# Sculpting: what works from code and how to teach it

Freehand brush strokes are a human skill. BlendWMe should usually TEACH sculpting or prepare the mesh, rather than fake strokes.

## Preparing a mesh to sculpt (good run_python steps)
- Start from a blockout. Voxel remesh to unify: obj.data.remesh_voxel_size = 0.02; then bpy.ops.object.voxel_remesh() with the object active.
- Or add a Multiresolution modifier and subdivide 2-3 times: m = obj.modifiers.new("Multires","MULTIRES"); bpy.ops.object.multires_subdivide(modifier="Multires", mode='CATMULL_CLARK').
- Switch to Sculpt Mode for the user: bpy.ops.object.mode_set(mode='SCULPT').

## Procedural "sculpt-like" detail from code
- Displace modifier with a Clouds/Voronoi texture for rock, bark, terrain.
- Proportional editing via bmesh: move vertices with a smooth falloff around a point to push/pull big forms.
- Shape keys for expressions.

## Teaching sculpting (Teach mode)
- Brushes to start with: Draw (X), Clay Strips (C), Smooth (Shift), Grab (G), Crease (Shift+C), Inflate (I). F changes radius, Shift+F strength.
- Turn on X symmetry (header, butterfly icon).
- Work big to small: Grab for forms, Clay Strips to build, Smooth to blend, Crease for details last.
- Check with teach_step + Accept (subjective) and look(mode="viewport") to comment on the result.
