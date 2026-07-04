"""Minimal raw-CDP client: attach to ONE page target's ws and Runtime.evaluate.

Bypasses Playwright's multi-target attach (which is stalling on this browser).
Reusable: import get_page_ws + RawCDP, or run as CLI to eval an expression.
"""
from __future__ import annotations
import io, json, sys, urllib.request
import websocket  # websocket-client (sync)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
GAME_HOST = "mushroomh5.acenetgame.com"


def get_page_ws(port: int, host: str = GAME_HOST) -> str:
    raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=8).read()
    for t in json.loads(raw):
        if t.get("type") == "page" and host in (t.get("url") or "") and "/pwa-sw" not in (t.get("url") or ""):
            return t["webSocketDebuggerUrl"]
    raise SystemExit(f"no page target for host={host} on {port}")


class RawCDP:
    def __init__(self, port: int, host: str = GAME_HOST, timeout: float = 20.0):
        # Chrome rejects ws upgrades carrying a disallowed Origin header
        # (--remote-allow-origins). suppress_origin omits it entirely.
        self.ws = websocket.create_connection(get_page_ws(port, host), timeout=timeout,
                                               suppress_origin=True,
                                               max_size=64 * 1024 * 1024)
        self._id = 0

    def _send(self, method: str, params: dict | None = None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result")

    def enable_runtime(self):
        try: self._send("Runtime.enable")
        except Exception: pass

    def evaluate(self, expression: str, return_by_value: bool = True, await_promise: bool = True):
        expr = expression.strip()
        res = self._send("Runtime.evaluate", {
            "expression": expr, "returnByValue": return_by_value,
            "awaitPromise": await_promise, "userGesture": True})
        if res and res.get("exceptionDetails"):
            raise RuntimeError(json.dumps(res["exceptionDetails"].get("exception", {}), ensure_ascii=False))
        return (res or {}).get("result", {}).get("value")

    def call(self, func_src: str, args: list):
        """Run a JS arrow-function source string with JSON args."""
        argjson = json.dumps(args, ensure_ascii=False)
        return self.evaluate(f"({func_src})({argjson})")

    def close(self):
        try: self.ws.close()
        except Exception: pass


def main():
    import argparse, base64
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--expr", default="1+1")
    ap.add_argument("--shot", default="", help="capture screenshot via CDP (browser-side) to this path")
    ap.add_argument("--reload", action="store_true", help="Page.reload (recovers a hung JS thread)")
    a = ap.parse_args()
    c = RawCDP(a.port, timeout=15.0)
    if a.shot:
        try: c._send("Page.enable")
        except Exception as e: print(f"Page.enable err: {e}")
        res = c._send("Page.captureScreenshot", {"format": "png"})
        with open(a.shot, "wb") as f:
            f.write(base64.b64decode(res["data"]))
        print(f"screenshot -> {a.shot}")
    elif a.reload:
        c._send("Page.reload", {"ignoreCache": False})
        print("Page.reload sent")
    else:
        c.enable_runtime()
        print("result:", json.dumps(c.evaluate(a.expr), ensure_ascii=False))
    c.close()


if __name__ == "__main__":
    main()
