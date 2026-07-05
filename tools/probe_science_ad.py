# ponytail: one-off live CDP probe for AD_SCIENCE_1 (科技園「跳過30分鐘」).
# Attaches to an already-open CDP page (default port 9226 = 小寶) and drives the
# real netManager.sendMessage — does NOT open a new WS login, so it won't kick
# the live session. See tasks/todo.md "科技園研究加速廣告接入純 WS".
import sys, os, json, base64
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright
from ws_token import codec, ad_reward

PORT = int(os.environ.get("PROBE_PORT", "9226"))  # 9226=小寶
HOST = "mushroomh5.acenetgame.com"

CMD_SCIENCE_INFO = 2817   # science.science_info_c2s/s2c (empty c2s body)
CMD_AD_INFO = 0x1601      # 5633
CMD_AD_REWARD = 0x1602    # 5634
CMD_ERROR = 0x0201        # 513
AD_SCIENCE_1 = 5

BLOCK = {260, 3330, 3331, 3332, 3333, 770, 804, 1801, 1805}

INSTALL_JS = r"""
(block) => {
  const nm = window.netManager && (window.netManager._cnet || window.netManager.cnet);
  if (!nm) return {ok:false, err:'no netManager._cnet'};
  window.__probe_block = new Set(block);
  window.__probe_ring = [];
  if (nm.__probe_send) { nm.sendMessage = nm.__probe_send; nm.__probe_send = null; nm.__probe_send_orig = null; }
  else if (nm.__probe_send_orig) { nm.sendMessage = nm.__probe_send_orig; nm.__probe_send_orig = null; }
  for (const rn of ['reciveMsg','receiveMsg','onMessage','_onMessage']) {
    if (nm['__probe_'+rn]) { nm[rn] = nm['__probe_'+rn]; nm['__probe_'+rn] = null; nm['__probe_'+rn+'_orig'] = null; }
    else if (nm['__probe_'+rn+'_orig']) { nm[rn] = nm['__probe_'+rn+'_orig']; nm['__probe_'+rn+'_orig'] = null; }
  }
  const push = (dir, cmd, data) => {
    try {
      if (typeof cmd === 'number' && window.__probe_block.has(cmd)) return;
      let b64 = null;
      if (data) {
        let u8 = null;
        if (data instanceof Uint8Array) u8 = data;
        else if (data instanceof ArrayBuffer) u8 = new Uint8Array(data);
        else if (data.buffer) u8 = new Uint8Array(data.buffer);
        if (u8) { let s=''; const cap=Math.min(u8.length, 65536); for (let i=0;i<cap;i++) s+=String.fromCharCode(u8[i]); b64 = btoa(s); }
      }
      window.__probe_ring.push({dir, cmd, len: data && (data.length||data.byteLength) || 0, b64, ts: window.performance.now()});
      if (window.__probe_ring.length > 3000) window.__probe_ring.shift();
    } catch(e){}
  };
  if (nm.sendMessage) {
    const orig = nm.sendMessage;
    nm.__probe_send_orig = orig;
    nm.sendMessage = function(cmd, data, ...rest) { push('TX', cmd, data); return orig.call(this, cmd, data, ...rest); };
  }
  for (const rn of ['reciveMsg','receiveMsg','onMessage','_onMessage']) {
    if (nm[rn]) {
      const orig = nm[rn];
      nm['__probe_'+rn+'_orig'] = orig;
      nm[rn] = function(cmd, data, ...rest) {
        if (typeof cmd === 'number') push('RX', cmd, data); else push('RX', null, cmd);
        return orig.call(this, cmd, data, ...rest);
      };
    }
  }
  return {ok:true, wrapped_send: !!nm.__probe_send_orig};
}
"""

DRAIN_JS = "() => { const r = window.__probe_ring||[]; window.__probe_ring = []; return r; }"
CLEAR_JS = "() => { window.__probe_ring = []; return true; }"

REPLAY_JS = r"""
(payload) => {
  const nm = window.netManager && (window.netManager._cnet || window.netManager.cnet);
  if (!nm) return {ok:false, err:'no netManager'};
  const hex = payload.hex;
  const u8 = new Uint8Array(hex.length/2);
  for (let i=0;i<u8.length;i++) u8[i] = parseInt(hex.substr(i*2,2),16);
  try { nm.sendMessage(payload.cmd, u8); return {ok:true, sent:u8.length}; }
  catch(e){ return {ok:false, err:''+e}; }
}
"""


def get_page(pw):
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    return browser, next(p for ctx in browser.contexts for p in ctx.pages
                         if HOST in (p.url or "") and "/pwa-sw" not in p.url)


def _pretty(data: bytes, indent: int = 0) -> str:
    lines = []
    for fn, val in codec.walk(data):
        pad = "  " * indent
        if isinstance(val, (bytes, bytearray)):
            sub = bytes(val)
            try:
                inner = _pretty(sub, indent + 1)
                if inner.strip():
                    lines.append(f"{pad}#{fn}: {{")
                    lines.append(inner)
                    lines.append(f"{pad}}}")
                else:
                    lines.append(f"{pad}#{fn}: (bytes, {len(sub)}) {sub.hex()}")
            except Exception:
                lines.append(f"{pad}#{fn}: (bytes, {len(sub)}) {sub.hex()}")
        else:
            lines.append(f"{pad}#{fn}: {val}")
    return "\n".join(lines)


def _send_and_wait(page, cmd: int, body: bytes, expect_cmds, timeout_ms=1500):
    page.evaluate(CLEAR_JS)
    r = page.evaluate(REPLAY_JS, {"cmd": cmd, "hex": body.hex()})
    if not r.get("ok"):
        print(f"[probe] send cmd={cmd} FAILED: {r}")
        return []
    page.wait_for_timeout(timeout_ms)
    rows = page.evaluate(DRAIN_JS)
    out = []
    for row in rows:
        if row["dir"] == "RX" and row.get("cmd") in expect_cmds:
            raw = base64.b64decode(row["b64"]) if row.get("b64") else b""
            out.append((row["cmd"], raw))
    return out


def read_science(page):
    replies = _send_and_wait(page, CMD_SCIENCE_INFO, b"", {CMD_SCIENCE_INFO})
    if not replies:
        print("[probe] science_info: NO REPLY")
        return None
    cmd, body = replies[-1]
    print(f"[probe] science_info_s2c raw ({len(body)} bytes):")
    print(_pretty(body, 1))
    return body


def read_ad5(page):
    replies = _send_and_wait(page, CMD_AD_INFO, b"", {CMD_AD_INFO})
    if not replies:
        print("[probe] ad_info: NO REPLY")
        return None
    cmd, body = replies[-1]
    counts = ad_reward.parse_ad_counts(body)
    info = counts.get(AD_SCIENCE_1) or {}
    print(f"[probe] ad[5]=AD_SCIENCE_1 count={info.get('count', 0)} "
          f"next_ts={info.get('next_ts', 0)} cap=4/day")
    return info


def claim_science_ad(page):
    body = ad_reward.build_ad_reward_body(AD_SCIENCE_1, is_free=1)
    replies = _send_and_wait(page, CMD_AD_REWARD, body, {CMD_AD_REWARD, CMD_ERROR})
    if not replies:
        print("[probe] claim: NO REPLY")
        return None
    cmd, raw = replies[-1]
    r = ad_reward.parse_ad_reward(cmd, raw)
    print(f"[probe] CLAIM success={r.success} cmd=0x{r.response_cmd:04x} "
          f"error_code={r.error_code} new_count={r.new_count} next_ts={r.next_ts}")
    return r


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "state"
    pw = sync_playwright().start()
    browser, page = get_page(pw)
    print(f"[probe] port={PORT} url={page.url}")
    print(json.dumps(page.evaluate(INSTALL_JS, sorted(BLOCK)), ensure_ascii=False))

    if cmd == "state":
        print("\n--- science_info (BEFORE) ---")
        read_science(page)
        print("\n--- ad_info[5] (BEFORE) ---")
        read_ad5(page)
    elif cmd == "claim":
        print("\n--- BEFORE ---")
        read_science(page)
        read_ad5(page)
        print("\n--- CLAIM ---")
        claim_science_ad(page)
        print("\n--- AFTER ---")
        read_science(page)
        read_ad5(page)
    else:
        print(f"unknown subcommand: {cmd} (use: state | claim)")

    pw.stop()


if __name__ == "__main__":
    raise SystemExit(main() or 0)
