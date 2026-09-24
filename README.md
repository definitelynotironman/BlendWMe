# BlendWMe 1.0

An AI Blender artist that lives inside Blender. It can build at full speed, slow down and show you how, or coach you while you do it yourself.

Built for the Nebius x NVIDIA Global AI Hackathon. It runs on Nebius Token Factory with NVIDIA Nemotron as the default agent.

## What changed from 0.6 (and why)

Version 1.0 is a rebuild. The old version wrapped a smaller model in a strict custom protocol: one JSON action per call, a 6,000-character rulebook, and recipes run on a copy of the scene. Each rule was another way for the model to fail.

MCP for Blender showed a better pattern, so 1.0 uses it:

| | 0.6 | 1.0 |
|---|---|---|
| How the model acts | Custom JSON, one action per call | Native tool calling, many steps in one conversation |
| How code runs | Filtered recipe language in a second Blender process | Normal Blender Python on the live scene, with an undo step before each run |
| Memory | Rebuilt context every call | One continuous conversation |
| Seeing | Viewport review | Multi-angle contact sheets judged by a vision model "art director" |
| Knowledge | Long prompt rules | Skill cards loaded on demand + live Blender API lookup |
| Code style | 3,700 dense one-liners | About 3,000 lines of plain, commented code |

What MCP does not have, and BlendWMe does: it lives in Blender, so you need no outside chat app. It has a Watch mode that builds the way a person would and explains each step. It has a Teach mode where you do the work and it checks you. You can take the wheel at any time and it continues from your version.

## Install

1. Edit > Preferences > Add-ons. Disable any older BlendWMe. Restart Blender.
2. Top-right menu > **Install from Disk** > choose `blendwme-1.0.0.zip`.
3. Make sure **Allow Online Access** is on (Preferences > System > Network).
4. In the 3D Viewport press **N** and open the **BlendWMe** tab.
5. Open **Connection**. Copy your Nebius key, then press **Paste Key**. Press **Test Connection**. It finds the exact model IDs on your account and checks that tool calling works.
6. Press **Test Vision**. It finds a vision model on your account that can see the viewport and saves it.

## Your API key

The key is **never** saved in Blender's preferences or in any `.blend` file. You choose where it lives:

| Option | Where | When to use |
|---|---|---|
| Password store (default) | Windows Credential Manager, macOS Keychain or Linux Secret Service, encrypted for your user account | Your own PC |
| This session only | Memory only, gone when Blender closes | Shared or lab PCs, live demos |
| Environment variable | `NEBIUS_API_KEY` | If you already manage keys that way |

How it stays safe:
- **Paste Key** reads the clipboard, stores the key, then clears the clipboard.
- The panel only shows the last 4 characters.
- The AI's own code is blocked from reading the clipboard, environment variables, the password store or BlendWMe's internals. The model can never print your key into the conversation.
- Copy Log never includes the key.
- **Forget Key** (bin icon) deletes it from memory and the password store. On Windows you can also see it under Credential Manager > Windows Credentials > `BlendWMe-Nebius`.

Good habits: make a separate Nebius key just for BlendWMe, so you can revoke it alone. Never paste it into code, GitHub, Devpost or a screen recording. If it leaks, revoke it in the Nebius console and paste a new one.

## First test (the house)

1. Open a new General scene. Choose **Auto**.
2. Type: *A cosy low poly cottage with a red tiled roof, a chimney, wooden door, four windows, a stone base and a small path. Stylised, warm colours.*
3. Press **New Task**. Watch the card at the bottom of the viewport.
4. When it finishes, type a follow-up: *Make the chimney taller and add a fence.* It remembers the whole project.

Then try the other modes on the same scene:

- **Watch:** *Show me how you would build a wooden fence.* Press **Next** after each step. Each step uses the operators a person would use (loop cut, extrude, modifiers), and the card tells you the shortcut.
- **Pause and Take Control:** while it works, press Pause. Move or reshape something. Press **Resume**. It is told exactly what you changed and builds around your version.
- **Teach:** *Teach me to model a simple table.* You do each step. It points at what matters, and the step completes by itself when your scene shows it is done. Press **Show me** if you are stuck.

## Controls

| Button | What it does |
|---|---|
| Send / New Task | Send continues the conversation. New Task starts fresh. |
| Tell BlendWMe | While it works, your message steers it at the next step. |
| Pause and Take Control | It stops after the current action. The scene is yours. |
| Resume / Continue | Hand back. It reads your changes first. |
| Next Step | Watch mode: go to the next step. Or set Auto-advance seconds. |
| I did it / Show me / Skip | Teach mode answers. |
| Undo AI Step | Same as Ctrl+Z. Every AI step is its own undo step. |
| Construction Notebook | Every step it ran, with its code, in a Text block. Good for learning and judges. |
| What the AI Saw | Opens the last picture the vision model judged. |

## Models

- **Agent:** Nemotron 3 Super by default (native function calling on Nebius). Try **Nemotron 3 Ultra** if your account has it. It is bigger and likely better at long agent work. Kimi K2.6 and any custom ID are also available for comparison.
- **Vision:** Test Vision tries Nemotron 3 Nano Omni first, then other vision models, and keeps the first that works.
- **Reasoning:** Low effort is the default. Use Full for hard briefs and Off for speed.

Do a quick bake-off: give the same house prompt to Super and Ultra and compare the result and the time. Pick the winner for your demo.

## How it works

```
you ─► panel ─► agent thread ──► Nebius (Nemotron, tool calling)
                    │  ▲
         tool calls │  │ results
                    ▼  │
        bridge (Blender main thread)
          ├─ get_scene_info / get_object_info   facts
          ├─ run_python                          hands (undo step first)
          ├─ look ─► vision model                eyes + art director
          ├─ load_skill / inspect_api            knowledge
          ├─ point_at / teach_step               teaching
          └─ ask_user / finish
```

| File | Job |
|---|---|
| `agent.py` | The loop. Notices your edits, asks the model, runs tools, waits in Watch/Teach |
| `llm.py` | OpenAI-compatible client for Nebius. Recovers tool calls written as text |
| `tools.py` | The tool list the model sees |
| `prompts.py` | The system prompt: workflow, quality bar, Python rules, mode rules |
| `executor.py` | Runs code on the live scene, reports errors with line numbers and what changed |
| `look.py` | Viewport, camera and multi-angle captures (ignores huge ground planes) |
| `scene_info.py` | Scene summaries, snapshots, and the diff that tells the AI what you changed |
| `knowledge.py` + `skills/` | Skill cards and live API lookup |
| `overlay.py` | The Graphite Paper card and red radar pointers |
| `ui.py`, `prefs.py` | Panel, buttons, settings |
| `credentials.py` | Keeps your API key in the OS password store, never in Blender files |
| `bridge.py` | Runs Blender work safely on the main thread |

### Making it a better artist

Add or improve cards in `skills/`. Each card is a short Markdown file: the workflow, good settings, code patterns and common mistakes. The first line is the description the AI sees. Test each card's code in Blender before you keep it. This is the main way to raise quality in new areas (characters, animation, sculpting) without touching the engine.

## When things go wrong

BlendWMe expects mistakes and is built to recover on its own:

| Problem | What happens |
|---|---|
| A step's code crashes | Everything that step created is removed, so the retry never leaves duplicates like `Roof.001`. The AI gets the failing line, the error and a plain-language hint (for example "you passed a name where Blender needs the object"), then runs the whole step again. You see STEP FAILED, then STEP (retry). |
| It fails twice in a row | It is told to change approach: smaller steps, bmesh instead of operators, or check names with inspect_api. |
| It fails 4 times | It pauses and hands you the wheel with the error on the card. Fix it, or type a hint and press Resume. No token-burning loops. |
| A step half-changed objects that already existed | It is told exactly which ones, so it can fix them instead of guessing. |
| Network trouble or the provider is busy | Automatic retry after 3, 8 and 20 seconds. |
| The conversation gets too long | Older steps are shortened and the request is retried. |
| The model writes a broken tool call | It is recovered when possible, or the model is asked to resend it. |
| The model only talks and stops working | It is nudged once to carry on. |
| Something still goes wrong | Press Pause or Stop at any time. Undo AI Step (Ctrl+Z) reverses any step. Copy Log gives a full report to share. |

## Safety and limits

- Code runs on your live scene, like MCP for Blender. Every step is undoable. A small blocklist stops file access, system commands and closing Blender. This is a guardrail, not a sandbox, so save your work first.
- Freehand sculpt strokes are not automated. BlendWMe prepares meshes and teaches sculpting instead.
- Video editing is supported through the sequencer API, but needs your own footage paths.
- Quality depends on the model. Nemotron 3 Super is a mid-size model. If results are weak, try Ultra and Full reasoning before changing code.

## Tests

- `tests/test_agent_headless.py` runs the real agent loop in headless Blender against a fake model server: building, error recovery, user-edit detection, pause mid-thought, Watch gating, Teach checks, Stop. (52 checks, including rollback, the stuck guard and key safety.)
- `tests/test_llm.py` tests the client helpers.

Viewport capture, multi-angle framing and the overlay card were checked in Blender 4.2.3. Live Nebius calls were not run in the build environment. Use Test Connection and Test Vision on your account.

## Credits

The offscreen capture and multi-angle framing in `look.py` adapt MCP for Blender by Siddharth Ahuja (MIT). See `LICENSE-THIRD-PARTY`.
