---
name: force-sleep-dashboard
description: Add or modify dashboard actions that immediately interrupt a running device task and force the device into sleep/idle, including control_panel_app.py, bot_state.py, new_main_v2.py, worker command routing, and templates/dashboard.html.
---

# Force Sleep Dashboard

Use this skill when the user asks to add or adjust a UI button/API that:
- stops the current task immediately
- cancels manual web hold or pending launch requests
- forces the device into sleep/idle
- must work for both local and worker-controlled devices

## Scope

Typical files:
- `control_panel_app.py`
- `bot_state.py`
- `new_main_v2.py`
- `runtime_services/device_runtime_service.py`
- `runtime_services/web_session_service.py`
- `worker_webhook_api.py`
- `templates/dashboard.html`

## Workflow

1. Add a one-shot state flag in `bot_state.py`.
2. Route the command through master and worker command paths.
3. Interrupt active loops at cooperative checkpoints.
4. Update UI button, confirmation text, and status labels.
5. Verify the device ends in sleep, not merely paused.

## Guardrails

- Prefer cooperative interruption over thread killing.
- Clear or cancel pending manual web launch requests when force sleep is requested.
- Keep `task`, `step`, and `next_wake_at` consistent with sleep state.
- Do not let a force-sleep request get consumed as a normal pause or skip-sleep event.

## Validation

- Run `python -m py_compile` on touched Python files.
- Check that the dashboard button appears for both local and remote devices.
- Confirm a forced sleep request can interrupt an active manual web hold.
