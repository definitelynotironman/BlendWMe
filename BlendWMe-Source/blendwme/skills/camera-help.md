# Camera help: aim, frame and choose a lens

Use when the user asks how to aim or frame the camera, why the render shows the wrong thing,
or when a brief needs a shot of the scene.

## The lesson (Teach): one teach_step each, under 20 words
1. Look through the camera: Numpad 0 (or View > Cameras > Active Camera).
   point_editor=VIEW_3D, point_region=HEADER. Why: the camera view is exactly what the render shows.
2. Line up the view you like first: leave the camera view, orbit and zoom until the scene looks right.
   point_editor=VIEW_3D. Why: it is easier to aim the viewport than to move a camera by hand.
3. Snap the camera to it: Ctrl+Alt+Numpad 0 (or View > Align View > Align Active Camera to View).
   point_editor=VIEW_3D, point_region=HEADER. Check: the camera moved.
4. Fine-tune inside the camera view: N sidebar > View > Lock Camera to View, then orbit and zoom.
   point_editor=VIEW_3D, point_region=UI. Why: the camera follows your navigation; turn it off after.
5. Choose the lens: select the camera, Properties > Camera tab (green camera icon) > Focal Length.
   point_tab=DATA. Why: 35 mm feels wide and spacious, 50 mm natural, 85 mm flatters faces.

Check expressions:
- Step 3: `C.scene.camera is not None and (C.scene.camera.matrix_world.translation - Vector((7.36, -6.93, 4.96))).length > 0.05`
- Step 5: read the lens first (get_object_info on the camera), then
  `C.scene.camera and abs(C.scene.camera.data.lens - <lens before>) > 0.5`

## Do it for me (after teach_step returns "show_me")
```python
cam = bwm.camera_to_view()          # the user's current view becomes the shot
bwm.frame_camera()                  # or: fit everything visible, keep the direction
bwm.focal_length(50)                # natural lens; moves back so the subject still fits
print(cam.name, tuple(round(v, 2) for v in cam.location), cam.data.lens)
```
- Frame one subject: `bwm.frame_camera("Cottage", "Chimney")`.
- Portrait: `bwm.focal_length(85, "Head")`. Wide room: `bwm.focal_length(28)`.
- Then look(mode="camera") and tell the user what changed.

## What good looks like
- The subject fills most of the frame with a little air around it; the horizon is level.
- Camera at eye height (about 1.5 m) for scenes, a little lower for heroic, higher for cosy overviews.
- Rule of thirds: the main subject slightly off-centre.

## Common mistakes
- Numpad keys on a laptop without a numpad: use the View menu, or Preferences > Input > Emulate Numpad.
- Lock Camera to View left on: the camera keeps moving while the user navigates. Turn it off.
- Clipping: far objects vanish when Clip End is too small (Camera tab > Lens > Clip End).
