# BlendWMe 0.2.3 · Native themed design shell

This is the **design phase** release based on the original v0.2 engine. The interface is drawn **inside Blender**, with Blender Grey as the default theme and Graphite Paper and Red/White available in its Settings page. It opens no browser and needs no pip packages.

## Install

1. Save your Blender scene. Disable/remove the earlier BlendWMe build, then restart Blender.
2. In Blender 4.2+, open **Edit → Preferences → Get Extensions → menu → Install from Disk**. Select `blendwme-0.2.3.zip` without extracting it. Enable the extension.
3. In the 3D View press **N → BlendWMe → Show / hide BlendWMe**.
4. Click **Settings** in the themed panel to choose a theme, scale, reduced motion, guides or compact layout. Theme and appearance settings belong to the scene.

Install as an extension. Do not copy the files into Startup or Application Templates.

## Included design

- Blender Grey: dark cards, off-white typography and orange guidance.
- Graphite Paper: pale paper, white cards, graphite outlines and red guidance.
- Red/White: white paper, pale rose cards, red controls and red guidance.
- Bundled Archivo Black headings and Patrick Hand body text. An OFL-compliant renamed oblique derivative of Patrick Hand supplies italic control references. The font licenses are included.
- Smooth single outlines, consistent spacing, high-resolution curve sampling and antialiased native GPU polyline strokes. The pencil arrow is sampled from the shipped `assets/pencil-arrow.svg`; SVG is not being embedded through a browser.
- Animated instruction blocks, expanding/fading radar rings without hiding the target icon, and a progressively drawn pencil circle and curved arrow.
- Reduced motion keeps indicators steady.
- Compact mode reduces the panel footprint; selecting Auto enables compact mode.
- An inline prompt field: click to type, use arrows/Home/End, Backspace/Delete, Ctrl+A or Ctrl+V, then Enter or the send arrow. Clicking outside ends keyboard focus; Esc leaves the field. Ordinary Blender interaction passes through outside the panel.
- User/AI ownership labels, mode tabs, a live connection footer, Pause and Stop controls, and optional step details through **Why?** or the instruction card. Scroll over the conversation/details to read more.
- Exact control references are italic and underlined. Clicking a BlendWMe control reference reveals and highlights that control, without executing it. References to mapped Blender controls highlight their pinned location. An unmapped reference asks you to pin it rather than guessing.
- Local v0.2 Lamp/Desk/Bevel chips remain for testing this shell. They are not the final product's task categories.

**Connection settings** currently opens Blender's own native properties dialog from the themed shell. This is an intentional native fallback for provider fields; the appearance settings and prompt are custom themed controls.

## What remains unchanged

The original v0.2 model worker, validated protocol, demo recipes and scene tools are preserved. This release does not expand the AI action set or integrate Tavily. Existing Build/Teach/Auto actions remain connected to the real v0.2 runtime, so the labels reflect actual state rather than a scripted UI demonstration. Local Demo is explicitly labelled no AI; configuring a key alone does not show Connected.

## Guides and control mapping

Use **Why? → Pin pointer in Blender**, return to Blender and click a real UI control. The click maps the ring and does not activate the control. Re-pin if the layout changes. Native Blender does not expose exact geometry for every button; this build preserves v0.2's honest observed/pinned targeting. Automatic screenshot-based button detection belongs to a future phase.

## Validation and your local review

Nine existing protocol tests passed. Headless Blender 4.2.17 checks cover native registration, real font loading/cleanup, three themes, layout bounds, settings controls, inline-input focus, non-executing reference highlights and Auto pause/stop proposals. See `docs/native-shell-checks.json`. No table/lamp modelling workflow was run and no paid AI request was made.

**Not visually verified in a GPU window on this machine.** Please review sharpness, font sizing, click targets, keyboard focus, theme switching and indicator animation on your Windows Blender 4.2.3. Headless checks do not establish visual parity with the concept images.

Use Local Demo for this review. Try theme switching, Compact/Expand, reduced motion and the inline prompt first; then try the existing Lamp/Desk workflow yourself. Emergency Pause/Stop remain in Blender's N panel.

Source: `extension/blendwme_ui.py` (native drawing/input), `ui_bridge.py` (v0.2 bindings), `overlays.py` (observed/pinned guides), `assets/` (vector paths), `fonts/` (bundled fonts).
