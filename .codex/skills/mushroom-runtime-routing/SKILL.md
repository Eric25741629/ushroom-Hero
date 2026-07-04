---
name: mushroom-runtime-routing
description: Update master/worker command propagation and runtime state transitions for this repo, including pause/resume, skip_sleep, manual_release, web_launch, worker webhook sync, and dashboard status updates.
---

# Mushroom Runtime Routing

Use this skill when the user asks to change how the automation runtime receives, stores, forwards, or displays control commands.

## Scope

Typical files:
- `bot_state.py`
- `control_panel_app.py`
- `worker_webhook_api.py`
- `runtime_services/worker_sync_service.py`
- `runtime_services/web_session_service.py`
- `runtime_services/device_runtime_service.py`
- `templates/dashboard.html`

## Workflow

1. Identify whether the command is local-only, master-to-worker, or cross-thread mailbox based.
2. Add the state flag or mailbox entry in `bot_state.py`.
3. Wire the command through the API route and worker sync path.
4. Make the runtime loop check the flag at cooperative checkpoints.
5. Update dashboard status text and action buttons if the user should trigger it manually.

## Guardrails

- Keep commands one-shot unless the user explicitly needs a persistent mode.
- Do not let command routing bypass the existing master/worker contract.
- Preserve backward compatibility for existing `paused`, `skip_sleep`, `manual_release`, and `web_launch` behavior.
- Keep UI state and backend state aligned; do not add a button without a matching runtime path.
- For `web_close`, Playwright objects are thread-affine: blocking/sleep loops may
  only peek the pending close flag to interrupt themselves. The owning device
  thread must consume the flag and call `device.close()`.

## Validation

- Confirm local and worker flows both receive the command.
- Check that remote commands are consumed exactly once unless they are intentionally persistent.
- Re-run the relevant dashboard and runtime compile checks after edits.
