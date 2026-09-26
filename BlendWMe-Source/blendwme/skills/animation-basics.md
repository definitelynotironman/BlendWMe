# Animation basics: keyframes, timing, the 12 principles in practice

## Keyframes in code
```python
obj.location = (0, 0, 0); obj.keyframe_insert("location", frame=1)
obj.location = (0, 0, 2); obj.keyframe_insert("location", frame=12)
obj.location = (0, 0, 0); obj.keyframe_insert("location", frame=24)
```
- Pose bones: pb.keyframe_insert("rotation_euler", frame=f).
- Shape keys: key_block.value then key_block.keyframe_insert("value", frame=f).
- Set the range: scene.frame_start, scene.frame_end. 24 fps is film style.

## Easing (4.2): after inserting, change interpolation
```python
for fc in obj.animation_data.action.fcurves:
    for kp in fc.keyframe_points:
        kp.interpolation = 'BEZIER'; kp.easing = 'AUTO'
```
## Principles that matter most
- Timing and spacing: fast = few frames, heavy = slow in and out.
- Squash and stretch: scale Z down and XY up on impact (keep volume), stretch while falling fast.
- Anticipation: a small move the opposite way before the main action.
- Follow through and overlap: parts (tail, hair, antenna) arrive 2-4 frames later.
- Arcs: limbs and heads move in arcs, not straight lines.

## Bouncing ball recipe (classic first lesson)
Frames 1 (top), 12 (contact, squash), 14 (stretch going up), 24 (top). Use Bezier with sharp contact keys (interpolation 'LINEAR' on the contact key).

## Walk cycle (simple)
Contact, down, passing, up, contact over 24 frames per two steps. Hips lowest at "down", highest at "passing". Arms swing opposite to legs.

## Check: look(mode="viewport") at several frames by setting scene.frame_set(f) in run_python, then look. Restore the frame afterwards.
