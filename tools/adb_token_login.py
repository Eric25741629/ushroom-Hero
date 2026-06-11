"""ADB-based login-credential extractor for the native Cocos client.

No browser, no SDK reversing. The native app (`com.mxdzz.tw.and`) dumps its full
login chain to logcat in plaintext on every cold start (tag `Cocos`, JS console).
We restart the app, scrape three lines, and reconstruct the WS login ticket that
`tools/_login_poc.py` (and the real `web_game_api` WS path) consume:

  1. server-list reply   ->  `get:{"list":[{ip,cp,cp2}], "gateway":{ip,...}}`
  2. login_auth request  ->  `.../oversea/login_auth?...&game_id=<fngid>&did=<...>`
  3. login_auth reply     ->  `{"code":0,"token":..,"p_key":..,"role_id":..,
                              "server_list":[{"game_server":..}], ...}`

The login_auth reply *is* the WS ticket. Per docs/protocol/AUTH_HANDSHAKE_SPEC.md
the `time` field is not validated and the ticket is reusable for hours, so one
restart mints a credential the bot can reuse until it expires.

Output: auth_state/_auth_capture_<device>.json  (same `{ "creds": {...} }` shape
as the CDP-based tools/_auth_capture_probe.py, so it is a drop-in for _login_poc).

Usage (run from repo root; `tools` is not a package, so call the file directly):
  python tools/adb_token_login.py                       # restart+scrape 5554, write creds
  python tools/adb_token_login.py --device emulator-5554 --verify
  python tools/adb_token_login.py --no-restart          # scrape current buffer only

Verified LIVE 2026-06-08 (emulator-5554, acct suid 27353216): restart -> scrape ->
role_login_s2c code=0 SUCCESS. The `--verify` WS login kicks the app's own session.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PACKAGE = "com.mxdzz.tw.and"
LAUNCH_ACTIVITY = f"{PACKAGE}/com.cocos.game.AppActivity"
PLAT = "2002"
# Anchor at the JSON start and let json.raw_decode find the balanced end
# (the log line continues past the object; a bounded regex would truncate it).
# 對應檔：utils/adb_token_scrape.py 以 importlib 重用下列 regex 與 build_creds()
# 做每次 adb 喚醒的被動撈取。改動這些 pattern 或 build_creds 請同步檢查那一支。
SCENE_LINE = re.compile(r'\{"list":\[.*?"gateway":\{.*')
AUTH_URL_RE = re.compile(r"/oversea/login_auth\?[^\s]*")
AUTH_REPLY_RE = re.compile(r'\{"code":\s*\d+.*?"p_key".*')


def _adb(device: str, *args: str, timeout: int = 30) -> str:
    out = subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    return out.stdout or ""


def _first_json(text: str) -> dict[str, Any] | None:
    """Decode the first balanced JSON object embedded in `text`."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def restart_app(device: str, user: int | None = None) -> None:
    """Force-stop and relaunch the game so the SDK re-mints a fresh login.

    user=None -> primary user 0 via monkey. user=N -> launch the dual-app /
    work-profile clone (e.g. Xiaomi XSpace = user 999) via `am start --user N`,
    which monkey cannot target."""
    _adb(device, "logcat", "-c")
    if user is None:
        _adb(device, "shell", "am", "force-stop", PACKAGE)
        _adb(device, "shell", "monkey", "-p", PACKAGE, "-c",
             "android.intent.category.LAUNCHER", "1")
    else:
        _adb(device, "shell", "am", "force-stop", "--user", str(user), PACKAGE)
        _adb(device, "shell", "am", "start", "--user", str(user),
             "-n", LAUNCH_ACTIVITY)


def scrape(device: str, deadline_s: float) -> dict[str, str]:
    """Poll logcat until the login_auth reply (with p_key) appears.

    Returns the raw matched strings: {serverlist, auth_url, auth_reply}.
    Keeps the LAST occurrence of each (handles SDK retries)."""
    found: dict[str, str] = {}
    end = time.time() + deadline_s
    while time.time() < end:
        time.sleep(3)
        buf = _adb(device, "logcat", "-d")
        if not buf:
            continue
        sl = SCENE_LINE.findall(buf)
        au = AUTH_URL_RE.findall(buf)
        ar = AUTH_REPLY_RE.findall(buf)
        if sl:
            found["serverlist"] = sl[-1]
        if au:
            found["auth_url"] = au[-1]
        if ar:
            found["auth_reply"] = ar[-1]
        if "auth_reply" in found and "serverlist" in found:
            return found
        elapsed = int(deadline_s - (end - time.time()))
        print(f"  ...{elapsed}s  "
              f"serverlist={'Y' if 'serverlist' in found else '-'} "
              f"auth_url={'Y' if 'auth_url' in found else '-'} "
              f"auth_reply={'Y' if 'auth_reply' in found else '-'}",
              flush=True)
    return found


def build_creds(found: dict[str, str], device: str) -> dict[str, Any]:
    reply = _first_json(found["auth_reply"])
    serverlist = _first_json(found["serverlist"])
    if not reply or reply.get("code") != 0:
        raise RuntimeError(f"login_auth reply missing/failed: {reply}")
    if not serverlist:
        raise RuntimeError("server-list line did not parse")

    srv = (reply.get("server_list") or [{}])[0]
    game_server = srv.get("game_server", "")
    gateway = (serverlist.get("gateway") or {}).get("ip", "")
    if not gateway or not game_server:
        raise RuntimeError(f"missing gateway/game_server (gw={gateway!r})")

    # fngid (game_id) + did from the login_auth URL
    url = found.get("auth_url", "")
    fngid = (re.search(r"[?&]game_id=([^&\s]+)", url) or [None, ""])[1] \
        if "game_id=" in url else ""
    did = (re.search(r"[?&]did=([^&\s]+)", url) or [None, ""])[1] \
        if "did=" in url else ""

    return {
        "uid": str(reply["uid"]),
        "uname": str(reply["uname"]),
        "plat": PLAT,
        "loginGameId": str(fngid),
        "loginSceneId": 0,                      # not returned; matches proven capture
        "roleId": int(reply["role_id"]),
        "isWhiteIp": int(reply.get("is_white_ip", 0)),
        "loginTime": int(reply["time"]),
        "pKey": str(reply["p_key"]),
        "loginTicket": str(reply["token"]),
        "ip": str(reply.get("ip", "")),
        "device_id": did,
        "device_name": device,
        "game_server": game_server,
        "gateway": gateway,
        "_ws_url": f"{gateway}?token={game_server}",
        "_captured_at": int(time.time()),
        "_source": "adb_logcat",
    }


def verify_ws(creds: dict[str, Any]) -> dict[str, Any]:
    """Reuse _login_poc framing to send role_login and check code==0.

    Returns {ok, code, role_id, serv_time} (ok False + error on timeout/exc)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _login_poc as poc  # noqa: E402
    import websocket  # noqa: E402

    ws = websocket.create_connection(creds["_ws_url"], timeout=20,
                                     suppress_origin=True)
    buf = bytearray()
    try:
        ws.send_binary(b"\x00")
        ws.send_binary(poc.gen_packet(poc.CMD_ROLE_LOGIN,
                                      poc.build_role_login(creds, creds["loginTime"])))
        ws.settimeout(15)
        end = time.time() + 20
        while time.time() < end:
            data = ws.recv()
            if isinstance(data, str):
                data = data.encode("latin1")
            buf += data
            for cmd, body in poc.drain_packets(buf):
                if cmd == poc.CMD_ROLE_LOGIN:
                    f = dict(poc._walk(body))
                    code = f.get(1)
                    print(f"[verify] role_login_s2c code={code} role_id={f.get(2)} "
                          f"serv_time={f.get(4)} => "
                          f"{'SUCCESS' if code == 0 else 'FAIL'}")
                    return {"ok": code == 0, "code": code,
                            "role_id": f.get(2), "serv_time": f.get(4)}
        print("[verify] timed out waiting for role_login_s2c")
        return {"ok": False, "code": None, "error": "timeout"}
    finally:
        ws.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="emulator-5554")
    ap.add_argument("--user", type=int, default=None,
                    help="Android user id of a dual-app clone (e.g. 999 for "
                         "Xiaomi XSpace). Omit for the primary user 0.")
    ap.add_argument("--no-restart", action="store_true",
                    help="scrape current logcat buffer without restarting the app")
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds to wait for the login chain (default 120)")
    ap.add_argument("--verify", action="store_true",
                    help="do a test WS role_login (WARNING: kicks the app session)")
    ap.add_argument("--out", default=None,
                    help="output path (default auth_state/_auth_capture_<device>.json)")
    args = ap.parse_args()

    if not args.no_restart:
        utxt = f" (user {args.user})" if args.user is not None else ""
        print(f"[adb_token] restarting {PACKAGE} on {args.device}{utxt} ...",
              flush=True)
        restart_app(args.device, args.user)
    print("[adb_token] scraping logcat for login chain ...", flush=True)
    found = scrape(args.device, args.timeout)
    if "auth_reply" not in found:
        print("[adb_token] ERROR: never saw a login_auth reply. The app may not "
              "have reached login (slow cold start?) or logging is disabled. "
              "Try a larger --timeout.", file=sys.stderr)
        return 2

    creds = build_creds(found, args.device)
    creds["_user"] = args.user
    out = Path(args.out) if args.out else \
        Path("auth_state") / f"_auth_capture_{args.device}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"creds": creds}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    redacted = {**creds, "loginTicket": creds["loginTicket"][:6] + "...",
                "game_server": creds["game_server"][:10] + "..."}
    print(f"[adb_token] wrote {out}")
    print(json.dumps(redacted, ensure_ascii=False, indent=2))

    if args.verify:
        result = verify_ws(creds)
        return 0 if result.get("ok") else 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
