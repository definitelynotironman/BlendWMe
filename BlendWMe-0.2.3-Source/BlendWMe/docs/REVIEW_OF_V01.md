# Review of the previous prototype

Source inspected: `jarvis-creative-tutor-prototype.zip`, generated 6 October 2026. This v0.2 is a substantial replacement of its single-file implementation, preserving the product modes and scene/tool boundary.

| v0.1 finding | v0.2 response |
|---|---|
| Synchronous `while` loop for Take Over | Main-thread timer, one action per operation, visible delay between actions |
| Synchronous 60-second HTTP call inside UI operator | Isolated request subprocess; poll, timeout, cancellation and temp cleanup |
| No Pause/Resume/Stop | Dedicated controls and stale-response checks |
| Teach Me returns a static lamp paragraph, no observation of human work | Per-action guides and live outcome verification |
| No overlay | Viewport teaching card, region highlights, object-origin ring and user-pinned exact UI rings |
| Mock scene depends on fixed names and positions | Unique per-task names, object identities, rename-aware history and live lamp/table anchors |
| Loose tool args with numeric ranges largely unchecked | Strict tool/arg allowlist, finite vectors, numeric bounds and booleans/types rejected |
| Existing material could be shared and changed | Dedicated material, mesh copy when shared |
| No normal Undo option on main agent operator | Each executed step is an UNDO-enabled Blender operator |
| No Nebius path | Configurable Nebius chat-completions adapter, session-only key and exact model field |
| Log text and latest status only | Native conversation list, selected-message detail, suggested prompts and full Text Editor transcript |

The previous README said live edits would be observed, but the local lamp mock placed the stem in fixed world coordinates. The new local lamp explicitly derives the next part's position from live base/stem dimensions and locations. This remains a narrowly defined recipe, not proof of a general spatial reasoning agent.
