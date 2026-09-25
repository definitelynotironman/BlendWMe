# How to teach well in Blender (read before Teach or Watch work)

## Principles
- One action per step. Say the shortcut AND the menu path for beginners: "Press Tab (or Object > Edit Mode)".
- Tell them where to look: point_at the object, or the editor/region (Properties editor, left toolbar TOOLS, N sidebar UI, header).
- Give exact values: "Bevel width 0.02 m, Segments 3".
- After each small lesson part, name the technique and why artists use it in one sentence.
- Celebrate real progress briefly. Never lecture.
- If they do something different, look at what they did and adapt. Their version is valid.

## Shortcuts beginners need (Blender 4.2, mouse over the 3D Viewport)
Tab edit/object mode. 1/2/3 in edit mode: vertex/edge/face select. G move, R rotate, S scale (then X/Y/Z to lock axis, type a number). E extrude. I inset. Ctrl+R loop cut. Ctrl+B bevel. Shift+A add menu. Shift+D duplicate. M move to collection. Ctrl+A apply transforms. Numpad . frame selected. Numpad 1/3/7 front/right/top. Z shading pie. N sidebar. Ctrl+Z undo.

## Useful checks for teach_step (Python expressions)
- Object exists: "'Table' in D.objects"
- Modifier added: "any(m.type=='BEVEL' for m in D.objects['Table'].modifiers)"
- Edit mode entered: "C.mode == 'EDIT_MESH'"
- Mesh got more faces: "len(D.objects['Table'].data.polygons) > 6"  (note: only updates in Object Mode)
- Material assigned: "len(D.objects['Table'].material_slots) > 0"
- Scale applied: "tuple(round(v,3) for v in D.objects['Table'].scale) == (1.0,1.0,1.0)"
- Keyframes exist: "D.objects['Ball'].animation_data is not None"
Leave check empty for subjective steps (sculpting, "shape it how you like") and let the user press I did it.

## Watch mode habits
Use the same operators a person uses (primitive_add, Edit Mode inset/extrude/loopcut, modifiers). Explain each in 1-2 sentences with the shortcut, and frame the result with bwm.frame(obj).
