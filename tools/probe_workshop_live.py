"""Live read-only probe of 加工坊 (workshop) state via the running game page.

Connects over CDP to the already-open browser (web_debug_port, default 9230),
finds the mushroomh5 game page, and via the in-page netManager reads:
  - 18434 worker_pw_info   (workshops + the un-parsed pw_worker_info#7 blob)
  - 18441 worker_pw_dining_hall (available foods)

Then recursively dumps every protobuf field (including the #7 detail blob) so we
can see the REAL "is processing / idle / out-of-materials" state, which the
existing parser only treats as worker_status>0.

Read-only: sends no mutate. Run while the bot is paused or live (reads are safe).

    python tools/probe_workshop_live.py [--port 9230]
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

CMD_INFO = 18434
CMD_DINING = 18441


def _read_varint(buf: bytes, i: int):
    shift = 0
    val = 0
    while i < len(buf):
        b = buf[i]
        val |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return val, i
        shift += 7
    raise ValueError("truncated varint")


def walk(buf: bytes):
    """yield (field_number, wire_type, value). value: int | bytes."""
    i = 0
    out = []
    while i < len(buf):
        tag, i = _read_varint(buf, i)
        fnum, wire = tag >> 3, tag & 7
        if wire == 0:
            val, i = _read_varint(buf, i)
        elif wire == 2:
            ln, i = _read_varint(buf, i)
            val = buf[i:i + ln]
            i += ln
        elif wire == 1:
            val = int.from_bytes(buf[i:i + 8], "little")
            i += 8
        elif wire == 5:
            val = int.from_bytes(buf[i:i + 4], "little")
            i += 4
        else:
            raise ValueError(f"bad wire {wire} at {i}")
        out.append((fnum, wire, val))
    return out


def looks_like_message(b: bytes) -> bool:
    """heuristic: does this bytes blob parse cleanly as protobuf?"""
    if not b:
        return False
    try:
        fields = walk(b)
    except Exception:
        return False
    return bool(fields)


def dump(buf: bytes, indent: int = 0):
    pad = "  " * indent
    try:
        fields = walk(buf)
    except Exception as e:
        print(f"{pad}<unparsable: {e}> raw={buf.hex()}")
        return
    for fnum, wire, val in fields:
        if wire == 2:
            if looks_like_message(val) and len(val) > 1:
                print(f"{pad}f{fnum} (msg, {len(val)}B):")
                dump(val, indent + 1)
            else:
                # show as bytes; try utf-8
                try:
                    s = val.decode("utf-8")
                    if s.isprintable():
                        print(f"{pad}f{fnum} = {s!r}")
                        continue
                except Exception:
                    pass
                print(f"{pad}f{fnum} = bytes({len(val)}) {val.hex()}")
        else:
            print(f"{pad}f{fnum} = {val}")


_RPC_JS = r"""
async ([cmd, bodyArr, timeoutSec]) => {
  const deadline = Date.now() + 5000;
  while (!window.netManager?._cnet) {
    if (Date.now() > deadline) throw 'netManager not ready';
    await new Promise(r => setTimeout(r, 100));
  }
  const sock = netManager._cnet;
  return new Promise((resolve, reject) => {
    const orig = sock.reciveMsg.bind(sock);
    let done = false;
    const cleanup = () => { if (!done){ done = true; sock.reciveMsg = orig; } };
    const tid = setTimeout(() => { cleanup(); reject('timeout'); }, timeoutSec * 1000);
    sock.reciveMsg = function (c, b) {
      if (c === cmd && !done) { clearTimeout(tid); cleanup(); resolve(Array.from(b)); }
      return orig(c, b);
    };
    try { sock.sendMessage(cmd, new Uint8Array(bodyArr)); }
    catch (e) { clearTimeout(tid); cleanup(); reject(String(e)); }
  });
}
"""


def call(page, cmd: int, body: bytes = b"") -> bytes:
    res = page.evaluate(_RPC_JS, [cmd, list(body), 6.0])
    return bytes(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9230)
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "mushroomh5" in (pg.url or ""):
                    page = pg
                    break
            if page:
                break
        if not page:
            print("no mushroomh5 page found on CDP")
            sys.exit(1)
        print(f"attached: {page.url}\n")

        print("=== 18434 worker_pw_info ===")
        info = call(page, CMD_INFO)
        print(f"raw {len(info)}B: {info.hex()}\n")
        dump(info)

        print("\n=== 18441 worker_pw_dining_hall ===")
        dining = call(page, CMD_DINING)
        print(f"raw {len(dining)}B: {dining.hex()}\n")
        dump(dining)


if __name__ == "__main__":
    main()
