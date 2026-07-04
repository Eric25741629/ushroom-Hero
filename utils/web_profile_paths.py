"""Single source for a web_h5 device's Chrome profile dir + auth-state file.

Both ``device_wrapper.PlaywrightGameDevice._start`` and
``control_panel.routes_web_session`` hand-rolled identical resolution logic.
The only divergence was a trailing ``os.path.normpath()`` on the dashboard
side, so a non-normalized configured path could make the manual-login profile
(dashboard) and the runtime profile (bot) resolve to different directory
*strings* — i.e. a restored login could land in a dir the bot never reads.

Both now go through these pure functions, unified on the normpathed form (same
physical directory, canonical string). ``_start`` keeps its own
``os.makedirs`` side effect — these functions only compute paths.
"""
from __future__ import annotations

import os

DEFAULT_PROFILE_DIR = "playwright_profile/{device_id}"
DEFAULT_STATE_FILE = "auth_state/{device_id}.json"


def resolve_profile_dir(device_id: str, configured: str | None = None) -> str:
    """Resolve the per-device Chrome ``--user-data-dir`` (absolute, normpathed).

    ``{device_id}`` / ``{ip}`` in *configured* are substituted; if neither is
    present the device_id is appended so devices never share a profile.
    """
    raw = str(configured or DEFAULT_PROFILE_DIR).strip() or DEFAULT_PROFILE_DIR
    profile_dir = raw.format(device_id=device_id, ip=device_id)
    if not os.path.normpath(profile_dir).endswith(device_id):
        profile_dir = os.path.join(profile_dir, device_id)
    if not os.path.isabs(profile_dir):
        profile_dir = os.path.join(os.getcwd(), profile_dir)
    return os.path.normpath(profile_dir)


def resolve_state_file(device_id: str, configured: str | None = None) -> str:
    """Resolve the per-device Playwright storage_state path (absolute, normpathed)."""
    raw = str(configured or DEFAULT_STATE_FILE).strip() or DEFAULT_STATE_FILE
    state_file = raw.format(device_id=device_id, ip=device_id)
    if "{device_id}" not in raw and "{ip}" not in raw:
        if os.path.basename(state_file).lower() == "auth_state.json":
            state_file = os.path.join(
                os.path.dirname(state_file), "auth_state", f"{device_id}.json"
            )
    if not os.path.isabs(state_file):
        state_file = os.path.join(os.getcwd(), state_file)
    return os.path.normpath(state_file)
