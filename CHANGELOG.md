# Changelog

All notable changes to BlendWMe. Versions follow the add-on's `blender_manifest.toml`.

## 2.2.0

### Added
- Five themes. New: **2000s**, made for Blender's Theme 2000 (Luna blue title bar, navy hard
  shadow, orange for your turn, Archivo text), and **Blender UI**, which draws no panel at all:
  BlendWMe lives in the sidebar (N > BlendWMe) with Blender's own widgets, like other extensions.
  Modes, brains, cards, plan, conversation, journal and settings are all there; the viewport
  pointers take the colours of your Blender theme.
- A **BlendWMe** button in the 3D Viewport's header: one way in for every theme.
- Three panel views: Full, Compact (what is happening, the buttons and the input) and the minimized
  bar. Double-click the logo row to switch; the square button makes the panel full height. The
  frame animates between views (200 ms in, 150 ms out) and follows Blender's Smooth View setting.
- Compact by itself when the viewport is too small for Full; the limit card keeps its Hand over and
  Wait buttons there.
- Typing like a Blender text field: selection with Shift, word jumps with Ctrl, Ctrl+A / C / X / V,
  undo and redo, click to place the caret, double-click to select a word.
- Settings open as a popover with Blender's own widgets (the same settings as Preferences > Add-ons).
- Blender 5.x support (tested on 5.2.2): the EEVEE engine id and the "one number for a vector"
  repair work on both 4.2 and 5.x.

### Changed
- A new renderer: every box, card and button is one shape with hard pixel edges, line widths and
  positions are whole pixels, and text is placed on whole pixels. Colours match the theme exactly.
- The panel follows Blender's Resolution Scale (Preferences > Interface) times its own Panel size.
- Move the panel in any direction: it snaps to the edges and corners of the free viewport (Ctrl
  turns snapping off, Esc puts it back), keeps the nearest corner and its distance from it, and never
  covers the header, the toolbar, the sidebar or the navigation gizmo.
- The panel's height follows its content. The corner grip sits opposite the panel's corner, and the
  width snaps to an 8-unit grid (minimum 300 x 220).
- Text never leaves its box: long lines end with an ellipsis, long words break with a hyphen,
  values keep their units ("10 s", "6 of 7"), and a last line is never a single orphan word.
- Note bubbles in the viewport move out of the panel's way; cue and working cards centre in the
  free part of the viewport.
- One redraw timer: about 30 frames a second only while something moves, and only for the regions
  that show it.
- The drawn settings screen and the sidebar launcher are replaced by the Settings popover and, in
  Blender UI mode, the full sidebar.

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
