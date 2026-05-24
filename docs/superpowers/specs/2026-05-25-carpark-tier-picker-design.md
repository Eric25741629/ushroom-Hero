# Dashboard: carpark tier picker

Date: 2026-05-25

## Goal

Let the user pick a device's cross-server parking tier (`carpark.cross_tier`) and
toggle the whole carpark feature on/off from the per-device settings modal in the
Flask dashboard, without editing `bot_config.json` by hand.

This is the smallest possible UI expansion — only the two controls the user
explicitly asked for. All other carpark fields stay in JSON with their existing
values.

## Non-goals

- Surfacing the other carpark fields (`avoid_lots`, `cluster`, `prefer_back`,
  `daytime_total`, `nighttime_total`, `daytime_cross`, `nighttime_cross`) in the
  UI. They keep their JSON values.
- Adding tiers other than silver to the picker. Silver is the only tier whose
  internal lot id range is verified by `carpark_auto.py`; offering other tiers
  would let the user select a path that crashes inside `_click_silver_lot_by_idx`.
- Reading or showing live carpark state (deployed counts, target counts) in the
  dashboard. That's a separate observability concern.

## User-visible behaviour

Inside the existing device settings modal (`#configModal`, opened by
`openSettings(ip)`), inside the `#webBackendGroup` block that already
shows/hides on the `web_h5` backend toggle, two new controls appear:

1. **Checkbox** `啟用跨服車位` — bound to `carpark.enabled`.
2. **Select** `跨服車座等級` — bound to `carpark.cross_tier`. One option only:
   `鉑銀 (silver)`. The dropdown form is intentional so future tiers can be
   added without touching the modal layout.

Behaviour:
- The select is enabled iff the checkbox is checked.
- When the checkbox is checked and the user clicks 儲存, the save payload also
  sets `experimental_cocos_navigation: true` for the device. This flag is the
  scheduler gating prerequisite (`utils.cocos_navigator._device_flag_enabled`).
  Unchecking the carpark checkbox does NOT clear the flag, because other future
  cocos-navigation features may depend on it.
- When the backend is `adb`, the carpark controls hide along with the rest of
  `#webBackendGroup`.

## Defaults when no carpark config exists yet

On modal open, if the device's config has no `carpark` block:
- Checkbox: unchecked (`enabled = false`)
- Select: `silver`

On save, if the user leaves the checkbox unchecked AND there was no prior
carpark block, the payload omits `carpark` entirely — no empty block written.

## The shallow-merge problem and the fix

`config_manager.update_device_config(ip, new_settings)` does
`current.update(new_settings)` — a shallow merge. POSTing
`{"carpark": {"enabled": true, "cross_tier": "silver"}}` would REPLACE the
entire `carpark` dict and wipe `avoid_lots`, `cluster`, daytime/nighttime
counts, etc.

The frontend fixes this by stashing the existing `config.carpark` dict on
modal open and spreading it on save, overriding only the two UI-managed keys:

```js
const cp = { ..._existingCarpark, enabled: chkEnabled.checked, cross_tier: selTier.value };
payload.carpark = cp;
if (chkEnabled.checked) payload.experimental_cocos_navigation = true;
```

This preserves any field the UI doesn't know about (including future fields).

### Backend correction discovered during validation

The first spec draft claimed no backend changes were needed. That was wrong.
The `GET /api/config/<ip>` endpoint calls `config_manager.get_device_config(ip)`,
which returns a typed `DeviceConfig` dataclass. The dataclass has a `_extra`
field for keys not in its known schema (carpark, statue_weekly,
experimental_cocos_navigation, enable_mount_sprint, mount_sprint_quantity, etc.).
Flask's `jsonify` serializes the dataclass via its `__dict__`, which emits
`_extra` as a NESTED object — `{"_extra": {"carpark": {...}, ...}, "name": ...}` —
instead of flattening unknown keys to the top level. So `config.carpark` in
the dashboard's `openSettings()` is `undefined`, `_existingCarpark` is `{}`,
and a save round-trip silently wipes the other 7 carpark fields.

The minimum fix: a new public helper in `config_manager.py` returning the raw
merged dict, and a one-line change in the Flask route to use it:

```python
# config_manager.py
def get_device_config_dict(ip: str) -> Dict[str, Any]:
    """Return device config as a raw dict (includes carpark, statue_weekly,
    experimental_cocos_navigation, and any other non-typed keys). Use this
    when serializing for the dashboard."""
    return _get_raw_device_config(ip)

# control_panel_app.py — GET /api/config/<ip>
return jsonify(config_manager.get_device_config_dict(real_ip))
```

Why a wrapper instead of using the underscore-prefixed `_get_raw_device_config`
directly: keeps the public/private convention. Why not modify `DeviceConfig`
to flatten `_extra` on jsonify: that would change behavior for every other
caller of `get_device_config`, far beyond this spec's scope.

**Restart caveat:** the Flask server runs with `debug=False, use_reloader=False`,
so picking up these backend changes requires restarting `new_main_v2.py`.

## Files to change

1. `templates/dashboard.html`
   - Modal: add two controls inside `#webBackendGroup` (after the
     `web_screenshot_method` field, before viewport width).
   - JS: extend `openSettings()` to read `config.carpark`, populate controls,
     stash `_existingCarpark`. Extend `saveConfig()` to build the merged
     payload as above. Wire the checkbox to enable/disable the select.

2. `config_manager.py`
   - Add new public function `get_device_config_dict(ip)` (one-line wrapper
     around the existing private `_get_raw_device_config`).

3. `control_panel_app.py`
   - GET `/api/config/<ip>` route: switch from `get_device_config` to
     `get_device_config_dict`. One-line change + a docstring explaining why.

`utils/carpark_auto.py`, `game_actions/carpark_scheduler.py` are untouched —
the existing `POST /api/config/<ip>` path already handles the merged payload.

## Validation / test plan

Manual, since the dashboard has no existing UI test harness:

1. Open dashboard, open settings for `emulator-5556`. Verify:
   - Checkbox is **checked** (config already has `enabled: true` from the prior
     bot_config.json edit).
   - Select shows `鉑銀 (silver)`.
2. Uncheck the checkbox, save. Verify in `bot_config.json`:
   - `carpark.enabled = false`
   - `carpark.avoid_lots` etc. unchanged (preserved by the stash-and-spread).
3. Re-check, save. Verify:
   - `carpark.enabled = true`
   - `experimental_cocos_navigation = true`
   - Other `carpark.*` fields unchanged.
4. Open settings for an `adb` backend device (e.g.
   `adb-fc65396d-4LPqmI...`). Verify the two new controls are **hidden** (they
   live inside `#webBackendGroup`).
5. Open settings for a `web_h5` device that has NO `carpark` block at all
   (none of the current 5 devices match — create one or temporarily delete the
   block). Verify:
   - Checkbox unchecked, select silver.
   - Save without checking → no `carpark` block appears in JSON.
   - Save WITH checking → new `carpark` block with `enabled: true, cross_tier:
     "silver"` (and no other keys).

No unit tests added — the change is presentation-only and goes through the
existing tested `update_device_config` path.

## What this does NOT solve

- 7fe98fc6 having no live CDP currently — that's a separate device-startup
  issue, unrelated to whether the dashboard can configure it.
- Silver being the only verified tier — adding gold/diamond requires verifying
  `POOL_TYPE_TO_ID` mapping + lot id base for each tier in `carpark_auto.py`
  first. Marked as a follow-up.
