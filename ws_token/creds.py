"""Login credentials for the ws_token backend.

Credentials are scraped from the native App's logcat by tools/adb_token_login.py
and written to auth_state/_auth_capture_<device>.json as ``{"creds": {...}}``.
This module loads that file into a typed, immutable Creds object.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = ROOT / "auth_state"

# JSON keys that must be present (others have safe defaults).
_REQUIRED = ("uid", "uname", "plat", "loginGameId", "roleId", "pKey", "loginTicket")


@dataclass(frozen=True)
class Creds:
    """Everything role_login_c2s (cmd 257) needs, plus the gateway WS url."""

    uid: str
    uname: str
    plat: str
    login_game_id: str
    role_id: int
    p_key: str
    login_ticket: str
    ws_url: str
    login_scene_id: int = 0
    is_white_ip: int = 0
    login_time: int = 0
    ip: str = ""
    device_id: str = ""
    device_name: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Creds":
        """Build from a captured creds dict. Raises ValueError on missing fields.

        ws_url prefers the captured ``_ws_url``; otherwise it is derived from
        ``gateway`` + ``?token=`` + ``game_server`` (AUTH_HANDSHAKE_SPEC §1/§4).
        """
        missing = [k for k in _REQUIRED if not d.get(k) and d.get(k) != 0]
        if missing:
            raise ValueError(f"creds missing required field(s): {', '.join(missing)}")

        ws_url = d.get("_ws_url")
        if not ws_url:
            gateway, game_server = d.get("gateway"), d.get("game_server")
            if not gateway or not game_server:
                raise ValueError(
                    "creds missing required field(s): _ws_url (or gateway+game_server)"
                )
            ws_url = f"{gateway}?token={game_server}"

        return cls(
            uid=str(d["uid"]),
            uname=str(d["uname"]),
            plat=str(d["plat"]),
            login_game_id=str(d["loginGameId"]),
            role_id=int(d["roleId"]),
            p_key=str(d["pKey"]),
            login_ticket=str(d["loginTicket"]),
            ws_url=str(ws_url),
            login_scene_id=int(d.get("loginSceneId", 0)),
            is_white_ip=int(d.get("isWhiteIp", 0)),
            login_time=int(d.get("loginTime", 0)),
            ip=str(d.get("ip", "")),
            device_id=str(d.get("device_id", "")),
            device_name=str(d.get("device_name", "")),
        )


def _capture_path(device: str, auth_dir: Path) -> Path:
    return auth_dir / f"_auth_capture_{device}.json"


def load_creds(device: str, *, auth_dir: Path = AUTH_DIR) -> Creds:
    """Load creds for ``device`` from auth_state/_auth_capture_<device>.json.

    Uses utf-8-sig so a UTF-8 BOM (common in this repo) does not corrupt the
    first key. Raises FileNotFoundError if the capture is missing.
    """
    path = _capture_path(device, auth_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"no captured creds for {device!r} at {path}; "
            f"run: python tools/adb_token_login.py --device {device}"
        )
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return Creds.from_dict(data["creds"])


def refresh_creds(
    device: str,
    *,
    user: int | None = None,
    auth_dir: Path = AUTH_DIR,
) -> Creds:
    """Re-mint a fresh ticket by shelling tools/adb_token_login.py, then reload.

    Thin wrapper only: the LIVE-verified scrape logic lives in that tool. Use
    when a ticket has expired (it cold-restarts the App, ~30s, and kicks the
    account's current session).
    """
    cmd = [sys.executable, str(ROOT / "tools" / "adb_token_login.py"),
           "--device", device]
    if user is not None:
        cmd += ["--user", str(user)]
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    return load_creds(device, auth_dir=auth_dir)
