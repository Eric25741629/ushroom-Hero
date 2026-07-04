"""Live choose_food experiment on a workshop, with before/after state read.

Confirms (a) what food_v means and (b) which recipe currently has materials.
Sends ONE choose_food (optionally a cancel first) and reads 18434 before+after.

    python tools/probe_choose_food.py --food 8001 --count 1 --ws 2 [--cancel] [--port 9230]

Read the printed pw_worker_info#7.f2 (selected food) + reply cmd / error_code.
"""
from __future__ import annotations
import argparse, sys
from playwright.sync_api import sync_playwright

CMD_INFO = 18434
CMD_CHOOSE = 18435
CMD_CANCEL = 18438
CMD_ERR = 0x0201


def _rv(b, i):
    s = v = 0
    while i < len(b):
        x = b[i]; v |= (x & 0x7F) << s; i += 1
        if not x & 0x80:
            return v, i
        s += 7
    raise ValueError("varint")


def walk(b):
    i = 0; out = []
    while i < len(b):
        tag, i = _rv(b, i); fn, w = tag >> 3, tag & 7
        if w == 0:
            v, i = _rv(b, i)
        elif w == 2:
            ln, i = _rv(b, i); v = b[i:i+ln]; i += ln
        elif w == 1:
            v = int.from_bytes(b[i:i+8], "little"); i += 8
        elif w == 5:
            v = int.from_bytes(b[i:i+4], "little"); i += 4
        else:
            raise ValueError("wire")
        out.append((fn, w, v))
    return out


def pb_uint(field, val):
    out = bytearray([field << 3])
    while val > 0x7F:
        out.append((val & 0x7F) | 0x80); val >>= 7
    out.append(val & 0x7F)
    return bytes(out)


def build_choose(food_k, food_v, ws):
    kv = pb_uint(1, food_k) + pb_uint(2, food_v)
    return bytes([0x0A, len(kv)]) + kv + pb_uint(2, ws)


_RPC = r"""
async ([cmd, body, expectErr, t]) => {
  const dl = Date.now()+5000;
  while(!window.netManager?._cnet){ if(Date.now()>dl) throw 'no net'; await new Promise(r=>setTimeout(r,100)); }
  const s = netManager._cnet;
  return new Promise((res, rej)=>{
    const orig = s.reciveMsg.bind(s); let done=false;
    const cl=()=>{ if(!done){done=true; s.reciveMsg=orig;} };
    const tid=setTimeout(()=>{cl(); rej('timeout');}, t*1000);
    s.reciveMsg=function(c,b){
      if((c===cmd || c===expectErr) && !done){ clearTimeout(tid); cl(); res({cmd:c, body:Array.from(b)}); }
      return orig(c,b);
    };
    try{ s.sendMessage(cmd, new Uint8Array(body)); }catch(e){ clearTimeout(tid); cl(); rej(String(e)); }
  });
}
"""


def call(page, cmd, body=b"", expect_err=None, t=6.0):
    r = page.evaluate(_RPC, [cmd, list(body), expect_err if expect_err is not None else -1, t])
    return r["cmd"], bytes(r["body"])


def show_ws(page, ws_team):
    _, info = call(page, CMD_INFO)
    for fn, w, v in walk(info):
        if fn == 2 and w == 2:
            d = {f: val for f, ww, val in walk(v) if ww in (0, 2)}
            team = d.get(1)
            status = d.get(3)
            f7 = d.get(7)
            food = None
            if isinstance(f7, (bytes, bytearray)):
                f7d = {f: val for f, ww, val in walk(bytes(f7)) if ww == 0}
                food = f7d.get(2)
            print(f"  team={team} worker_status={status} selected_food(f7.f2)={food}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--food", type=int, required=True)
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--ws", type=int, default=2)
    ap.add_argument("--cancel", action="store_true")
    ap.add_argument("--port", type=int, default=9230)
    a = ap.parse_args()
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{a.port}")
        page = next((pg for ctx in b.contexts for pg in ctx.pages if "mushroomh5" in (pg.url or "")), None)
        if not page:
            print("no page"); sys.exit(1)
        print("BEFORE:"); show_ws(page, a.ws)
        if a.cancel:
            try:
                c, body = call(page, CMD_CANCEL, pb_uint(1, a.ws), expect_err=CMD_ERR)
                print(f"cancel reply cmd={c} body={body.hex()}")
            except Exception as e:
                print(f"cancel: {e}")
        body = build_choose(a.food, a.count, a.ws)
        print(f"choose body: {body.hex()}  (food={a.food} count={a.count} ws={a.ws})")
        try:
            c, rb = call(page, CMD_CHOOSE, body, expect_err=CMD_ERR)
            if c == CMD_ERR:
                code = next((v for f, w, v in walk(rb) if f == 1 and w == 0), None)
                print(f"REJECTED 0x0201 error_code={code}")
            else:
                print(f"OK reply cmd={c} body={rb.hex()}")
        except Exception as e:
            print(f"choose: {e}")
        print("AFTER:"); show_ws(page, a.ws)


if __name__ == "__main__":
    main()
