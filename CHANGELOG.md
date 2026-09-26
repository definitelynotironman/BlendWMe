# Changelog

All notable changes to BlendWMe. Versions follow the add-on's `blender_manifest.toml`.

## 2.1.0

### Added
- The BlendWMe panel in the approved Graphite Paper design, drawn inside the 3D Viewport:
  the logo, a status pill, the brain card with Hand over, mode tabs, two distinct voices in the
  conversation (YOU and the brain), a thinking box with ticks and a scribble line, cue cards with a
  blinking block, the Auto plan card with a segmented bar, the limit card with the baton summary,
  a settings screen and a journal screen.
- Three themes: Graphite, Red and Blender Grey. Animations follow the system's reduce-motion setting.
- Bundled fonts: Archivo, Archivo Black and Patrick Hand (SIL Open Font License 1.1).
- Indicators in Blender: radar rings on objects and Properties tabs, pencil circles around parts of
  the window, note bubbles, a marching outline around what is being built, and a working card when
  the panel is closed.
- Keyboard shortcuts: Ctrl+Alt+M opens BlendWMe; Enter, Shift+Enter, Esc, Esc twice, Up/Down history,
  Shift+Tab, Ctrl+L, `/` commands and `?`. They are editable in Preferences > Keymap and act only
  while the panel has focus.
- Camera help: `bwm.camera_to_view`, `bwm.frame_camera`, `bwm.focal_length` and a camera-help
  technique card with a Teach lesson.
- Stability: BlendWMe pauses work during renders and modal tools, survives file loads and undo while
  a brain works, starts each part safely, keeps a session log and a Python crash trace, and makes a
  crash report zip.
- Journal export to the logs folder.
- A movable, resizable and minimizable panel: drag the logo row to move it (it snaps back to the
  right edge), drag the corner grip to resize it, and fold it into a small status bar without
  stopping the session. Take control minimizes it by itself (a setting).
- Panel size, Text size and Icon size settings, and "Put it back on the right".

### Changed
- Runs on Claude Code and Codex only. The pay-per-token API engine is removed.
- Fewer settings: the main settings fit on one screen; the rest are under Advanced settings.
- The sidebar tab is now a small launcher with references and extras.
- The panel is smaller by default (75 %) and docks tight against the right edge of the viewport.
- Ctrl+Alt+M opens BlendWMe, then focuses the input, then minimizes the panel.
- Teach cards hold one action in under 20 words, always point at the real control, and give the
  menu path for numpad keys.

### Fixed
- The Done card no longer stays forever: every card can be closed, and the Done card folds into
  one line after 6 seconds.
- The plan cannot get stuck: the task can only finish when every plan part is built, checked or
  skipped with a reason, and the plan card closes when a task finishes, stops or a new brief starts.
- A claimed render must exist before the task can finish.
- Answers, plan parts, step titles, notes and logs are no longer cut off. Long messages fold behind
  "Read more".
- After a crash Blender could leave BlendWMe switched off with no hint why. Now each part starts
  safely, problems show in the sidebar with a crash report button, and a reminder asks you to save
  preferences so BlendWMe stays on.

## 2.0.0

### Added
- Claude Code (on a Claude plan) as the main brain and Codex CLI (on a ChatGPT plan) as the second
  brain, started by the add-on with no pay-per-token key needed.
- The BlendWMe tools server (MCP) and a local socket bridge to Blender with a random token.
- The project journal ("the baton"), saved inside the .blend and next to it, with a handoff brief.
- Hand over between brains, a usage-limit card and automatic hand-over in Auto.
- Setup checks for Claude Code, Codex and the tools server, and an install button for the server.
