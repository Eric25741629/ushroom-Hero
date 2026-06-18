"""Set a team workshop to a recipe at its EXACT producible count, parsed live.

Algorithm (the one the bot fix will use):
  1. cancel the workshop  -> server pushes 0x0402 with the refunded/current
     material stock for the recipe's inputs.
  2. parse the material counts (6017/6019/6020/6021) from that 0x0402 push.
  3. producible = min over approach[(mat, per_unit)] of floor(stock[mat]/per_unit).
  4. choose_food(food, producible).
  5. verify by re-reading 18434 f7.f2 == food (the ack is off-channel).

configFood.approach (CDP-confirmed): 8001:[[6017,2]] 8002:[[6017,1],[6019,2]]
  8004:[[6017,1],[6019,2],[6020,2]] 8005:[[6019,2],[6020,2],[6021,2]] 8003: none

    python tools/workshop_set_parsed.py --food 8005 --ws 2 [--port 9230]
"""
from __future__ import annotations
import argparse, sys
from playwright.sync_api import sync_playwright

APPROACH = {
    8001: [(6017, 2)],
    8002: [(6017, 1), (6019, 2)],
    8004: [(6017, 1), (6019, 2), (6020, 2)],
    8005: [(6019, 2), (6020, 2), (6021, 2)],
    8003: [],
}
CMD_INFO, CMD_CHOOSE, CMD_CANCEL = 18434, 18435, 18438


def _rv(b, i):
    s = v = 0
    while i < len(b):
        x = b[i]; v |= (x & 0x7F) << s; i += 1
        if not x & 0x80:
            return v, i
        s += 7
    raise ValueError


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
            raise ValueError
        out.append((fn, w, v))
    return out


def pb_uint(field, val):
    out = bytearray([field << 3])
    while val > 0x7F:
        out.append((val & 0x7F) | 0x80); val >>= 7
    out.append(val & 0x7F)
    return bytes(out)


def build_choose(k, v, ws):
    kv = pb_uint(1, k) + pb_uint(2, v)
    return bytes([0x0A, len(kv)]) + kv + pb_uint(2, ws)


# capture 0x0402 frames while sending cancel, then return parsed material counts
_CANCEL_SNIFF = r"""
async ([ws]) => {
  const cap=[]; const s=netManager._cnet; const orig=s.reciveMsg.bind(s);
  s.reciveMsg=function(c,b){ if(c===1026) cap.push(Array.from(b)); return orig(c,b); };
  function vu(f,v){const o=[f<<3];while(v>0x7f){o.push((v&0x7f)|0x80);v>>=7;}o.push(v&0x7f);return o;}
  s.sendMessage(18438, new Uint8Array(vu(1,ws)));
  await new Promise(r=>setTimeout(r,1200));
  s.reciveMsg=orig; return cap;
}
"""

_SEND = r"""
async ([cmd, body, t]) => {
  const s=netManager._cnet;
  return new Promise((res)=>{ const tid=setTimeout(()=>res(null),t*1000);
    const orig=s.reciveMsg.bind(s);let d=false;
    s.reciveMsg=function(c,b){ if(!d&&(c===cmd||c===513)){d=true;clearTimeout(tid);s.reciveMsg=orig;res({cmd:c,body:Array.from(b)});} return orig(c,b); };
    try{ s.sendMessage(cmd,new Uint8Array(body)); }catch(e){ if(!d){d=true;clearTimeout(tid);s.reciveMsg=orig;res({err:String(e)});}}
  });
}
"""


def parse_materials(frames):
    counts = {}
    for fr in frames:
        for fn, w, v in walk(bytes(fr)):
            if fn == 2 and w == 2:
                d = {f: val for f, ww, val in walk(bytes(v))}
                iid, c = d.get(1), d.get(3)
                if isinstance(iid, int) and isinstance(c, int):
                    counts[iid] = c
    return counts


def selected_food(page, team):
    r = page.evaluate(_SEND, [CMD_INFO, [], 5.0])
    if not r or "body" not in r:
        return None
    for fn, w, v in walk(bytes(r["body"])):
        if fn == 2 and w == 2:
            d = {f: val for f, ww, val in walk(v)}
            if d.get(1) == team:
                f7 = d.get(7)
                if isinstance(f7, (bytes, bytearray)):
                    return {f: val for f, ww, val in walk(bytes(f7))}.get(2)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--food", type=int, required=True)
    ap.add_argument("--ws", type=int, default=2)
    ap.add_argument("--team", type=int, default=6002)
    ap.add_argument("--port", type=int, default=9230)
    a = ap.parse_args()
    approach = APPROACH.get(a.food)
    if approach is None:
        print(f"unknown food {a.food}"); sys.exit(1)
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{a.port}")
        page = next((pg for ctx in b.contexts for pg in ctx.pages if "mushroomh5" in (pg.url or "")), None)
        if not page:
            print("no page"); sys.exit(1)
        frames = page.evaluate(_CANCEL_SNIFF, [a.ws])
        mats = parse_materials(frames)
        print(f"parsed material stock from 0x0402: {mats}")
        if approach:
            producible = min(mats.get(m, 0) // per for m, per in approach)
        else:
            producible = 9999
        print(f"food={a.food} approach={approach} -> producible={producible}")
        if producible < 1:
            print("producible < 1 (out of materials) — leaving idle"); return
        page.evaluate(_SEND, [CMD_CHOOSE, list(build_choose(a.food, producible, a.ws)), 4.0])
        got = selected_food(page, a.team)
        print(f"after choose: selected_food(team {a.team}) = {got}  "
              f"{'OK' if got == a.food else 'FAILED'}")


if __name__ == "__main__":
    main()
