# BlendWMe 2.0

An AI Blender artist that lives inside Blender. It can build at full speed, slow down and show you how, or coach you while you do it yourself. You can let go of the wheel and take it back at any moment: it continues from your version.

2.0 runs on the subscriptions you may already have. **Claude Code** (Claude Pro or Max) is the main brain and **Codex CLI** (ChatGPT Plus or Pro) is the second brain. There is no pay-per-token API by default. When one brain reaches its usage limit, the other takes over from the same project journal.

## What is new in 2.0

| | 1.0.5 | 2.0 |
|---|---|---|
| The brain | One model through a pay-per-token API | Claude Code and Codex on your subscriptions; the API brain stays, off by default |
| How the brain reaches Blender | The add-on's own loop | BlendWMe's MCP server (`blendwme_mcp`), the same 13 tools for every brain |
| Memory | The conversation | The **project journal**, saved with the .blend: the brief, your decisions, the plan, every step signed by the brain that made it, open struggles, reviews, handoff notes |
| Switching brains | Not possible | **Hand over to Codex / Claude** in the panel. The other brain gets a handoff brief and a fresh look at the scene, fixes the open struggles first and builds on the other brain's work |
| Usage limits | Not relevant | Detected. A card offers the hand-over; in Auto mode it can switch by itself |
| Setup | Paste an API key | Setup checks in the panel: is Claude installed and signed in, is Codex, does the MCP server start |

Everything else stays the same whichever brain works: Auto / Watch / Teach, Piece by Piece / Full Build with the plan checklist, Pause and Take Control, the Graphite Paper card and the red pointers.

## Install

1. Install **Claude Code** and sign in once: open a terminal, run `claude` and sign in with your Claude account.
2. Optional, for the relay: install the **Codex CLI** and sign in once: run `codex` and choose **Sign in with ChatGPT**.
3. Download `blendwme-2.0.0.zip` from this repo's **Releases** page (or build it: `python tools/package.py` inside `BlendWMe-Source` writes it to `BlendWMe-Source/dist/`).
4. Blender 4.2 or newer: Edit > Preferences > Add-ons. Disable any older BlendWMe. Restart Blender.
5. Top-right menu > **Install from Disk** > choose `blendwme-2.0.0.zip`.
6. In the 3D Viewport press **N** and open the **BlendWMe** tab.
7. Open the **Brains** panel and press **Install MCP server** once. It makes a small Python environment in the workspace folder (`Documents/BlendWMe Workspace`) and installs the official `mcp` package (this needs the internet once). You need Python 3.10 or newer on your PC; if none is found, Blender's own Python is used.
8. Press **Check All**. You should see three green lines: Claude, Codex (if you installed it) and the MCP server.

BlendWMe never reads, copies or stores your Claude or ChatGPT sign-in. Sign-in happens only inside the official `claude` and `codex` programs. The setup checks use their own status commands and never show your e-mail.

## First test (the cottage)

1. Open a new General scene. Choose **Auto** and the **Claude** brain.
2. Type: *A cosy low poly cottage with a red tiled roof, a chimney, wooden door, four windows, a stone base and a small path. Stylised, warm colours.*
3. Press **New Task**. Claude's reply streams into the panel while the cottage is built piece by piece.
4. Type a follow-up: *Make the chimney taller and add a fence.* It remembers the whole project.

Then try the relay:

- Press **Hand over to Codex**. Claude writes a short handoff note, and Codex continues from the journal and a fresh look at the scene.
- Press **Hand over to Claude**. Claude comes back to its own conversation, is told exactly what Codex changed, fixes anything Codex struggled with first, and does not undo Codex's work.
- Save the .blend, close Blender and open the file again. The journal comes back with it, and each brain resumes where it left off.

And the other modes on the same scene:

- **Watch:** *Show me how you would build a wooden fence.* Press **Next** after each step. Each step uses the operators a person would use, and the card tells you the shortcut.
- **Pause and Take Control:** while it works, press Pause. Move or reshape something. Press **Resume**. It is told exactly what you changed and builds around your version.
- **Teach:** *Teach me to model a simple table.* You do each step. It points at what matters, and the step completes by itself when your scene shows it is done. Press **Show me** if you are stuck.

### Testing the MCP server from a terminal

The **Brains** panel has **Copy Terminal Command**. Paste it into a terminal: it starts an ordinary interactive `claude` session in the workspace folder with BlendWMe's tools. Ask it to build something and watch it appear in Blender.

## Controls

| Button | What it does |
|---|---|
| Brain (Claude / Codex / API) | Which brain answers your next message |
| Send / New Task | Send continues the conversation. New Task starts fresh |
| Tell BlendWMe | While it works, your message reaches it with its next tool result |
| Hand over to Codex / Claude | Pass the work to the other brain. It turns red when a usage limit is reached |
| Pause and Take Control | It stops after the current action. The scene is yours |
| Resume / Continue | Hand back. It reads your changes first |
| Next Step | Watch mode: go to the next step. Or set Auto-advance seconds |
| I did it / Show me / Skip | Teach mode answers |
| Undo AI Step | Same as Ctrl+Z. Every AI step is its own undo step |
| Construction Notebook | Every step it ran, with its code, in a Text block |
| Project Journal | The journal and the handoff brief, in a Text block |
| What the AI Saw | Opens the last picture the art director judged |

## Settings (Preferences > Add-ons > BlendWMe)

- **Claude:** program path (empty: found automatically), model and effort (empty: your Claude Code defaults).
- **Codex:** program path and model.
- **Relay:** *Auto mode: hand over by itself at a usage limit* (on by default; in Watch and Teach you are always asked) and *Ask for a handoff note when handing over* (uses a little of the old brain's usage; the journal is complete without it).
- **MCP bridge:** start automatically, workspace folder, the Python for the MCP server.
- **API brain:** off by default. If you switch it on, it uses an OpenAI-compatible endpoint with your key (pay per token) and stops at a hard token budget. A local Ollama model can use it through Ollama's OpenAI-compatible endpoint.

## How it works

```
you ─► panel ─► relay ─► engine ─► claude -p / codex exec (your subscription)
                  ▲                        │ MCP (stdio)
         events: text, tool_call,          ▼
         tool_result, done, error,   blendwme_mcp server (outside Blender)
         limit_reached                     │ local socket, 127.0.0.1 + a random token
                                           ▼
                           socket_bridge ─► tool_host ─► bridge (Blender main thread)
                                              │  ├─ run_python      hands (undo step first, rollback on failure)
                                              │  ├─ look            eyes (multi-angle pictures to the brain)
                                              │  ├─ get_scene_info / get_object_info / inspect_api / load_skill
                                              │  ├─ plan / point_at / teach_step / ask_user / finish
                                              │  └─ handoff_note / read_journal
                                              ▼
                                         journal ("the baton", saved with the .blend)
```

- Claude Code runs as `claude -p` with streaming JSON, its own session (`--session-id`, then `--resume`), only BlendWMe's MCP tools allowed (no shell, no file editing) and BlendWMe's instructions appended to its system prompt.
- Codex runs as `codex exec --json` in a read-only sandbox, with BlendWMe's MCP server added for that run only (`-c` overrides; your Codex config is never edited), and resumes its own thread.
- Both brains start in the workspace folder, which holds the shared instructions (`CLAUDE.md` and `AGENTS.md`), the MCP config and the socket's connection file.
- Pay-per-token API keys in your environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and similar) are removed before a brain starts, so the brains always use your subscription sign-in.

| File | Job |
|---|---|
| `relay.py` | The panel's one entry point: active brain, turns, hand-over, limits |
| `engines/` | `claude_code.py`, `codex.py`, `api.py` (the 1.0.5 loop), `limits.py` (usage-limit messages), `base.py` |
| `journal.py` | The project journal and the handoff brief, saved in the .blend and next to it |
| `socket_bridge.py`, `workspace.py` | The local socket for the MCP server, the workspace folder |
| `tool_host.py` | BlendWMe's tools for outside brains (the same behaviour as the 1.0.5 tools) |
| `setup_checks.py`, `mcp_probe.py` | The setup buttons; a tiny MCP client to check the server |
| `blendwme_mcp/` | The MCP server program (official `mcp` SDK, stdio) |
| `agent.py`, `llm.py` | The 1.0.5 agent loop and client: the API brain |
| `tools.py`, `prompts.py` | The tool list and the shared instructions for every brain |
| `executor.py`, `repair.py`, `precheck.py` | Run code on the live scene; repair mechanical API slips; reject what cannot be repaired |
| `look.py`, `scene_info.py` | Captures; scene summaries, your edits, who made each object |
| `knowledge.py` + `skills/` | Skill cards and live API lookup |
| `helpers.py`, `shapes.py`, `furniture.py`, `coerce.py` | The bwm modelling kit |
| `overlay.py`, `ui.py`, `prefs.py`, `state.py`, `bridge.py`, `credentials.py`, `logs.py` | Overlay, panel, settings, threading, key storage (API brain), logs |

## The project journal

BlendWMe, not the AI, holds the memory. The journal records the brief and your decisions, the plan with the brain that built each part, every step (code and result), who made each object (Claude, Codex, API or you), struggles that stay open until a later step solves them, the latest art director review and the brain's verdict, handoff notes and every switch.

From this it builds a compact handoff brief (with a size limit) for the brain that takes over: the brief, open struggles first, the latest note, what changed since that brain last worked here, the plan, who made each object, the latest review, and the rule to build on the other brain's work and never undo it. Any brain can also call `read_journal` at any time.

It is saved as a Text block named *BlendWMe Journal* inside the .blend, and as `<file>_blendwme/journal.json` next to it. When you open the file, the newer copy wins.

## The bwm modelling kit

Instead of making the brain fight bmesh, BlendWMe gives it a tested, forgiving kit, available inside run_python as `bwm.<name>`:

| Group | Functions |
|---|---|
| Shapes | `box`, `cylinder`, `cone`, `sphere` (or ellipsoid), `torus`, `prism`, `wedge`, `extrude_profile`, `loft`, `lathe`, `tube`, `limb`, `gable_roof` |
| Modifiers, kept live | `bevel`, `mirror`, `array`, `subdivide`, `solidify`, `boolean` |
| Placement | `bounds`, `top`, `place_on`, `align`, `rotate`, `duplicate`, `parent`, `group` |
| Architecture | `wall`, `floor`, `opening`, `window`, `door`, `path` |
| Furniture | `bed`, `desk`, `table`, `chair`, `wardrobe` / `closet`, `shelf`, `monitor`, `keyboard`, `pc_tower`, `rug`, `lamp`, `plant` |

Every function takes `name, sizes, location, collection, material` in that order and everything else by keyword. Arguments are forgiving (numbers as text, colours in many forms, common alias names). Re-running a step with the same names replaces the AI's own earlier objects; your objects are never replaced. `inspect_api('bwm.gable_roof')` shows the full help.

### Making it a better artist

Add or improve cards in `skills/`. Each card is a short Markdown file: the workflow, good settings, code patterns, common mistakes and how to teach it. The first line is the description the AI sees. A card is only kept after its code runs cleanly in Blender 4.2.

## The art director

- **References first.** Reference images are looked at before anything is built.
- **Reviews without being asked.** After the first build steps and then every few steps, BlendWMe sends the brain four angles of the scene with a rubric: match to the brief and reference, silhouette and proportions, contact, shape detail, colour and value, then the most valuable fixes. The review lists every AI-made object with its exact size, so leftovers and wrong scale are easy to spot.
- **One check before finishing.** The first time the brain calls finish, the result is reviewed once more if anything changed.

## When things go wrong

| Problem | What happens |
|---|---|
| A small mechanical API slip in the code | It is repaired and the step runs. The brain is told what was fixed |
| A step's code crashes | Everything that step created is removed, the brain gets the failing line and a plain-language hint, and the struggle goes into the journal |
| The same step keeps failing | It is told to change approach, then it pauses and hands you the wheel |
| A brain reaches its usage limit | A card offers the hand-over (automatic in Auto mode if you allow it). Nothing is lost: the journal was written all along |
| A brain lost its saved conversation | It starts a fresh one with the full context |
| A brain or the MCP server is missing or not signed in | The setup checks say what to do, in one sentence |
| Something still goes wrong | Press Pause or Stop at any time. Undo AI Step (Ctrl+Z) reverses any step |

## Full logs

After every task, BlendWMe saves the whole conversation to **Documents/BlendWMe Logs**: a readable `.md` file (every message, the code of every step, every result) and a `.json` file. Use **Save Full Log** to save at any moment and **Open Logs Folder** to find them.

## Safety and limits

- Code runs on your live scene, like MCP for Blender. Every step is undoable. A small blocklist stops file access, system commands and closing Blender. This is a guardrail, not a sandbox, so save your work first.
- The socket listens on 127.0.0.1 only and needs a random token that changes each time the bridge starts.
- Claude Code gets only BlendWMe's tools. Codex runs in a read-only sandbox (Codex has no switch to remove its shell completely).
- Freehand sculpt strokes are not automated. BlendWMe prepares meshes and teaches sculpting instead.
- Quality depends on the brain and on the skill cards.

## Tests

Run these from `BlendWMe-Source/`. None of them use a subscription or an API key.

- `tests/test_v2_headless.py`: the socket bridge, the tool host and the real MCP server end to end; the relay with a fake `claude` and a fake `codex` (streaming, limits, stop, lost sessions, the hand-over in both directions, the journal on save and reopen, auto-relay); the setup checks. Needs the `mcp` package in `BlendWMe-Source/.venv` for the MCP parts:
  `blender -b --factory-startup --python-exit-code 1 --python tests/test_v2_headless.py`
- `tests/test_agent_headless.py`: the 1.0.5 agent loop (the API brain) against a fake model server, the bwm kit and the example cards (192 checks).
- `tests/test_mcp_server.py`: the MCP server with a fake Blender socket (run with the venv's Python).
- `tests/test_llm.py`: the client helpers.
- `tools/package.py` builds `dist/blendwme-<version>.zip` (the add-on plus the MCP server).

## Credits

The offscreen capture and multi-angle framing in `look.py` adapt MCP for Blender by Siddharth Ahuja (MIT). See `LICENSE-THIRD-PARTY`. The MCP server uses the official MCP Python SDK.

## Changelog

- **2.0.0**: Claude Code and Codex as brains on your subscriptions; BlendWMe's MCP server and the local socket bridge; the project journal saved with the .blend; Hand over between brains with handoff notes and a handoff brief; usage-limit detection and auto-relay in Auto mode; setup checks and Install MCP server in the panel; the pay-per-token API brain is off by default with a hard token budget.
- **1.0.5**: the reliability floor. `import bwm` works. Mechanical API slips are repaired instead of rejected. The forgiving bwm kit. Worked example cards for the cottage, room and ice cream cone. Harness-driven art director reviews with an object inventory, a final check and converging critique. Smarter stuck guard. Tool calls written as text are recovered. The 1.0.5 notes are in `docs/NEBIUS_NOTES.md`.
- **1.0.4**: tool arguments sent as text are converted using each tool's schema.
- **1.0.3**: Python-as-tool calls are rescued; hidden collections relinked; checks for swapped `modifiers.new` arguments and wrong node types.
- **1.0.2**: plan checklist, Piece by Piece / Full Build, quiet self-correction.
- **1.0.1**: vision model discovery, pre-check of API names, full logs.
- **1.0.0**: rebuild on the MCP pattern: native tool calling, run_python on the live scene, look, skills, Auto / Watch / Teach, rollback of failed steps.
