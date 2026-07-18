# -*- coding: utf-8 -*-
"""B 端 HTTP：接收 combat payload，在免洗帳 CDP 頁跑 BattleMainServer。

用法::

    python -m battle_calc.server --cdp-port 9240 --http-port 18765

需先開好同網址已登入的 H5（免洗號）並開 remote-debugging-port。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from .simulate import SIM_JS

GAME_HOST = "mushroomh5.acenetgame.com"

_lock = threading.Lock()
_page = None
_pw = None


def _connect(cdp_port: int):
    global _page, _pw
    from playwright.sync_api import sync_playwright

    if _page is not None:
        return _page
    _pw = sync_playwright().start()
    browser = _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
    page = next(
        (
            p
            for ctx in browser.contexts
            for p in ctx.pages
            if GAME_HOST in (p.url or "") and "pwa-sw" not in (p.url or "")
        ),
        None,
    )
    if page is None:
        raise RuntimeError(f"no game page on CDP {cdp_port}")
    _page = page
    return page


def _sim(cdp_port: int, mode: str, combat: dict) -> dict:
    from .modes import build_sim_request

    with _lock:
        page = _connect(cdp_port)
        req = build_sim_request(mode, combat)
        return page.evaluate(SIM_JS, req)


class Handler(BaseHTTPRequestHandler):
    cdp_port: int = 9240

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            try:
                page = _connect(self.cdp_port)
                ok = page.evaluate(
                    "() => !!(window.netManager && typeof System !== 'undefined')"
                )
                self._json(200, {"ok": bool(ok), "cdp_port": self.cdp_port})
            except Exception as e:
                self._json(200, {"ok": False, "err": str(e), "cdp_port": self.cdp_port})
            return
        self._json(404, {"ok": False, "err": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/simulate":
            self._json(404, {"ok": False, "err": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            mode = body.get("mode") or (body.get("request") or {}).get("mode")
            combat = body.get("combat") or {}
            if not mode or not combat:
                self._json(400, {"ok": False, "err": "need mode + combat"})
                return
            out = _sim(self.cdp_port, str(mode), combat)
            self._json(200, out if isinstance(out, dict) else {"ok": False, "err": "bad sim"})
        except Exception as e:
            self._json(500, {"ok": False, "err": str(e)})


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="Battle calc B server")
    p.add_argument("--cdp-port", type=int, default=9240)
    p.add_argument("--http-port", type=int, default=18765)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args(argv)
    Handler.cdp_port = args.cdp_port
    try:
        _connect(args.cdp_port)
        print(f"B page connected CDP {args.cdp_port}", flush=True)
    except Exception as e:
        print(f"WARN: CDP not ready yet: {e}", flush=True)
    httpd = ThreadingHTTPServer((args.host, args.http_port), Handler)
    print(f"battle_calc server http://{args.host}:{args.http_port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
