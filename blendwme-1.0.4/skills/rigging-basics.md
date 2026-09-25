# Rigging basics: armatures, weights, posing

## Simple rig in code (limb or character)
```python
arm_data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", arm_data); C.scene.collection.objects.link(rig)
bwm.select(rig); bpy.ops.object.mode_set(mode='EDIT')
eb = arm_data.edit_bones
root = eb.new("Spine"); root.head = (0, 0, 0.9); root.tail = (0, 0, 1.3)
neck = eb.new("Head"); neck.head = root.tail; neck.tail = (0, 0, 1.7); neck.parent = root; neck.use_connect = True
bpy.ops.object.mode_set(mode='OBJECT')
```
- Name bones with .L / .R suffixes so Blender can mirror poses (UpperArm.L).
- Bones go through the centre of the limb, with joints at real joint positions.

## Binding the mesh
- Separate rigid parts (toy look): parent each part to a bone and keep it where it is:
```python
C.view_layer.update()
world = part.matrix_world.copy()
part.parent = rig; part.parent_type = 'BONE'; part.parent_bone = "Head"
part.matrix_world = world
```
- Single mesh: select mesh then rig (rig active) and bpy.ops.object.parent_set(type='ARMATURE_AUTO') for automatic weights.

## Posing
```python
bwm.select(rig); bpy.ops.object.mode_set(mode='POSE')
pb = rig.pose.bones["UpperArm.L"]; pb.rotation_mode = 'XYZ'; pb.rotation_euler = (0, 0, math.radians(-30))
bpy.ops.object.mode_set(mode='OBJECT')
```
## Rigify (full human rig): enable the Rigify add-on in Preferences (ask the user), add the Human meta-rig, align it, then Generate Rig.

## Mistakes: forgetting to leave Edit/Pose mode, bones pointing the wrong way, scale not applied on the mesh before binding.
