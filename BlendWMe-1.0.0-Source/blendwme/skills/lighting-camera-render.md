# Lighting, camera and render setup

## Three-point light (objects, characters)
- Key: AREA light, 45 degrees to the side and above, energy 300-1000 W for a ~2 m subject, size 1-2 m, warm (1.0, 0.92, 0.8).
- Fill: opposite side, 1/3 of key energy, cool (0.8, 0.88, 1.0), bigger size.
- Rim: behind and above, pointing at the subject's back edge, strong enough to outline the silhouette.
- Outdoor: SUN light strength 3-5, angle about 45 degrees, plus a light blue World colour (0.5, 0.65, 0.9) strength 0.6-1.

```python
light = bpy.data.lights.new("Key", 'AREA'); light.energy = 600; light.size = 1.5
key = bpy.data.objects.new("Key", light); C.scene.collection.objects.link(key)
key.location = (3, -3, 4)
d = Vector((0, 0, 1)) - key.location
key.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
```

## Camera
- Lens 35-50 mm for scenes, 85 mm for portraits. Camera height around eye level (1.5 m) or slightly low for heroic.
- Aim with the same to_track_quat('-Z','Y') trick at the subject centre. Rule of thirds: subject off-centre.
- scene.camera = cam_obj. Check with look(mode="camera").

## Render
- EEVEE in 4.2: scene.render.engine = 'BLENDER_EEVEE_NEXT'. Cycles: 'CYCLES'.
- Resolution 1920x1080. Colour management view transform "AgX" (default in 4.x).
- Do not start a long final render yourself. Set it up and tell the user to press F12.
