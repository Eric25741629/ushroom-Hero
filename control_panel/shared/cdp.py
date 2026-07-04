"""web_h5 裝置的 CDP Runtime.evaluate 共用層。

⚠ tests 會 monkeypatch ``control_panel_app._cdp_json_response``：各 blueprint
路由呼叫它時必須透過 façade 模組屬性（晚綁定），不可直接 import 函式。
"""
import json
import time

from flask import jsonify

import config_manager


def _cdp_evaluate(ip, expression, await_promise=False, timeout=15):
    """Execute JS on a web_h5 device via CDP. Returns (result_dict, error_str).

    ``timeout`` bounds BOTH the ws connect and the result-wait loop; raise it for
    long-running injected flows (e.g. the carpark walk/execute that drives the UI
    over many seconds). Defaults to 15s to preserve existing callers' behaviour.
    """
    import websocket as _ws
    from runtime_services.live_view_bridge import find_game_page_target

    cfg = config_manager.get_device_config(ip)
    debug_port = cfg.get("web_debug_port")
    if not debug_port:
        return None, "no web_debug_port"

    ws_url = find_game_page_target(
        debug_port, "mushroomh5.acenetgame.com", timeout_sec=5.0
    )
    if not ws_url:
        return None, f"no CDP target on port {debug_port}"

    try:
        ws = _ws.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        payload = json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        })
        ws.send(payload)
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == 1:
                ws.close()
                return msg.get("result", {}), None
        ws.close()
        return None, "timeout"
    except Exception as exc:
        return None, str(exc)


def _cdp_err_code(err: str) -> int:
    """Map a _cdp_evaluate error string to an HTTP status code (single source).

    Was duplicated verbatim here and in routes_fly_pet.fly_pet_shelve (cx-1).
    """
    if err == "no web_debug_port":
        return 400
    if "no CDP target" in err:
        return 502
    return 500


def _cdp_json_response(ip, expression, await_promise=False, data_key="data", timeout=15):
    """Helper: evaluate JS, parse JSON string result, return Flask response."""
    result, err = _cdp_evaluate(ip, expression, await_promise=await_promise, timeout=timeout)
    if err:
        return jsonify({"status": "error", "message": err}), _cdp_err_code(err)
    inner = result.get("result", {})
    exc_detail = result.get("exceptionDetails")
    if exc_detail:
        return jsonify({"status": "error", "message": str(exc_detail)}), 500
    if inner.get("type") == "string":
        try:
            parsed = json.loads(inner["value"])
            if isinstance(parsed, dict) and "error" in parsed:
                return jsonify({"status": "error", "message": parsed["error"]}), 500
            return jsonify({"status": "ok", data_key: parsed})
        except Exception:
            return jsonify({"status": "ok", "raw": inner["value"]})
    return jsonify({"status": "ok", data_key: inner.get("value", inner)})


# Shared JS helper for extracting a pet's lock + star flags. Canonical rule:
# scan pet.ext for entry k===2 (lock) / k===1 (star); fall back to pet.lock.
# Single source of truth so fly_pet_list and fly_pet_find_pair stay consistent.
# Returns an object: {lock, star}. Callers read .lock (and .star) as needed.
# Pure literal (no { } interpolation) so it injects safely into both raw-strings
# and f-strings (in f-strings interpolate via a placeholder, not doubled braces).
_FLY_PET_LOCK_JS = (
    "(function(pet){"
    "var lock=0; var star=0; var ext=pet.ext||[];"
    "if(Array.isArray(ext)){"
    "for(var i=0;i<ext.length;i++){var x=ext[i];"
    "if(x && x.k===2) lock=x.v; if(x && x.k===1) star=x.v;}"
    "} else {"
    "for(var ek in ext){var x=ext[ek];"
    "if(x && x.k===2) lock=x.v; if(x && x.k===1) star=x.v;}"
    "}"
    "if(!lock && pet.lock!==undefined) lock=pet.lock;"
    "return {lock:lock, star:star};"
    "})"
)
