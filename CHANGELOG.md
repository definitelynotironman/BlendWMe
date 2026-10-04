# Changelog

All notable changes to BlendWMe. Versions follow the add-on's `blender_manifest.toml`.

## 2.3.0

### Added
- **New task** / **New lesson** at the bottom of the panel and in the sidebar (also `/new`): start
  fresh without touching the scene. The old task stays in the journal's history.
- Formatted replies in the panel: **bold**, bullet lists and `code`. The Blender UI sidebar can't
  mix font weights, so it shows them as clean plain text and opens the reader for the full version.
- A full-text reader (**Full text**, **Draft**): scroll, select with the mouse, Ctrl+A, Ctrl+C, and
  Shift+Ctrl+C for the original text with its formatting marks.
- Teach: **Hint** and **Skip this step** next to **Check my work** and **Do it for me**. Lesson
  checks read the scene directly (selection, active object, mode, modifiers, properties) and work in
  Edit Mode. When the step was already done before you started, it says so instead of praising you.
- A backup copy of the scene before every AI step. A failed step restores what it changed; when it
  can't, the result says where the backup is.
- Limits on very heavy geometry and on scripts that run too long, so a step can't freeze Blender.

### Changed
- Two modes: **Auto** and **Teach**. Together (Watch) is part of Auto now: pause, take over and
  resume instead of pressing Next after every step. Old files and settings open in Auto.
- Finishing needs evidence: a fresh look at the result, every plan part settled, and no open "needs
  work" review. A look only counts when a picture actually arrived, and a step that changed nothing
  doesn't tick a plan part.
- Your materials and shared meshes are copied before the AI changes them, and parts you attached to
  the AI's objects survive when it rebuilds them.
- The journal recognises objects after a rename, tracks more kinds of edits (shape, modifiers,
  materials, shape keys, animation), follows the saved file instead of discarded work, and notices
  moved or missing reference pictures.
- Only the tested technique cards are offered: the cottage, the room, the ice cream cone and camera
  help.
- Each Blender has its own tools-server folder, so two Blender windows on one computer don't mix up
  their work.
- The 2000s theme uses Patrick Hand for writing again, the caret lines up with the letters at every
  text size, and box and badge edges are smooth.
- The code is reorganised into documented classes. Nothing changes for the AI's modelling kit.

### Fixed
- File > New (or opening a file) no longer leaves the panel without working buttons until Blender
  restarts.
- The Settings popover no longer closes as soon as you let go of the button.
- Size settings like `diameter` are halved correctly, and arguments the kit can't use are refused
  instead of silently dropped.
- A tool request from an old task or a stopped session can no longer change the scene.
- The MCP server reported version 2.0.0; it now reports the add-on's version.

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
