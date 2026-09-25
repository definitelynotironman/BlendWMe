# Video editing in the Video Sequencer (VSE)

## Setup
```python
scene = C.scene
if not scene.sequence_editor:
    scene.sequence_editor_create()
seqs = scene.sequence_editor.sequences
clip = seqs.new_movie("Clip1", filepath="//footage/clip1.mp4", channel=1, frame_start=1)
music = seqs.new_sound("Music", filepath="//audio/music.mp3", channel=3, frame_start=1)
title = seqs.new_effect("Title", type='TEXT', channel=4, frame_start=1, frame_end=72)
title.text = "My Film"; title.font_size = 80
```
- File paths must come from the user. Never guess file names. Ask with ask_user.
- Cut: strip.frame_final_start / frame_final_end. Fade: add a keyframe on strip.blend_alpha (or volume for sound).
- Match scene fps and resolution to the footage. scene.frame_end = last strip end.
- Teach: the Video Editing workspace, K to cut at the playhead, G to move, Ctrl+Shift+C... prefer menus for beginners: Strip > Split.
