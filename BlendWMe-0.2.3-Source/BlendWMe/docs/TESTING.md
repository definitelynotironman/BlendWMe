# Verification and limits

Development platform: Linux, Blender **4.2.17 LTS**. The extension advertises Blender 4.2 minimum. Windows and macOS packaging paths are implemented but not exercised here.

## Completed automated checks

- 9 pure Python protocol/recipe tests: unknown tools, malformed vectors, nonfinite/range checks, response structure, fenced JSON, scene signature semantics and live lamp anchors.
- 25 Blender checks: registration, preview without scene mutation, one-action pause, skip, user movement/scale/rename, stop, Teach Me zero mutation, human-created primitive verification, identity adoption, autonomous scheduling, manual-edit pause, shared material/mesh isolation, six primitive types, geometry fingerprints, absent API key in saved `.blend`, and unregister cleanup.
- 12 request/process checks in real Blender: nonblocking request start, response preview, worker termination, cancelled temp cleanup, stale response rejection, unsafe action rejection and mode-switch cancellation. Four of the checks confirm exit from THINKING in different scenarios.
- Blender extension manifest validation.
- 5 UI/Undo checks: actual guide/sidebar rendering, calibrated Add-menu ring, Undo removing the applied object and invalidating the task. The resulting screenshots were visually inspected and the viewport-label overlap was fixed.
- Actual ZIP installation and enablement using Blender's extension manager.

Machine-readable results are in `blender-test-results.json`, `provider-test-results.json`, and `ui-test-results.json`. The HTTP fixture is explicitly **not a real model**. There was no live Nebius/Nemotron/Ollama inference during these tests.

## Visual QA

The screenshots in `screenshots/` are captured from an isolated actual Blender 4.2 window. They are not image-generated mockups. The product overlay draws with Blender's GPU API. The QA scripts use a disposable test scene, not the user's work.

## Manual acceptance on your machine

| Test | Expected result |
|---|---|
| Install ZIP | Extension enables and N → BlendWMe appears |
| Lamp + Build With Me | Proposed base waits for Apply, then task pauses |
| Move/scale/rename base | Later stem follows live base translation and height |
| Skip material | Current material stays unchanged |
| Desk + Take Over | Separate visible changes; Blender remains navigable |
| Pause before next action | No new geometry while paused |
| Manual edit during Take Over | Agent pauses at the next observation; Resume re-observes |
| Stop while thinking | No action from that cancelled response |
| Teach desk | Guide appears; extension doesn't create the cube |
| Check without doing step | Guide explains what's missing |
| Create matching cube yourself | Check accepts it and next guide refers to your object |
| Pin Add menu | Exact mint ring; click does not activate menu |
| Resize/layout/scroll | Reset/re-pin if pointer no longer marks the correct control |
| Undo | Blender reverses its operation and task stops |
| Save/reopen blend | Objects/conversation remain; task resets and API key is absent |
| Real Nebius request | Requires key, valid model ID and Blender Online Access |

Not validated: network service model availability, real inference quality, arbitrary workflows, heavily constrained/parented objects, repeated scene switching during pinning, and production security hardening.
