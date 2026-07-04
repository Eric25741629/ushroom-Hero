# ponytail: one-off live WS capture for 小寶 weekly-reward selection. Not a reusable tool.
import sys, os, json, base64
from playwright.sync_api import sync_playwright

PORT = int(os.environ.get("PROBE_PORT", "9226"))  # 9226=小寶, 9224=5558
HOST = "mushroomh5.acenetgame.com"

# noise cmds (decimal): 0x104 heartbeat, module-13 spam d02/d03/d04/d05,
# periodic syncs 302/324/709/70d
BLOCK = {260, 3330, 3331, 3332, 3333, 770, 804, 1801, 1805}

INSTALL_JS = r"""
(block) => {
  const nm = window.netManager && (window.netManager._cnet || window.netManager.cnet);
  if (!nm) return {ok:false, err:'no netManager._cnet'};
  window.__probe_block = new Set(block);
  window.__probe_ring = [];
  // Fully restore to the PRISTINE original before re-binding. Earlier installs
  // saved the bound original as nm.__probe_send (deepest) and/or a wrapper as
  // nm.__probe_send_orig. Prefer the deepest (true original) to undo all layers.
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

# Replay: send cmd with given hex body via the game's real sendMessage.
REPLAY_JS = r"""
(payload) => {
  const nm = window.netManager && (window.netManager._cnet || window.netManager.cnet);
  if (!nm) return {ok:false, err:'no netManager'};
  const hex = payload.hex;
  const u8 = new Uint8Array(hex.length/2);
  for (let i=0;i<u8.length;i++) u8[i] = parseInt(hex.substr(i*2,2),16);
  // use the wrapped sendMessage (will also log TX); orig is fine too
  try { nm.sendMessage(payload.cmd, u8); return {ok:true, sent:u8.length}; }
  catch(e){ return {ok:false, err:''+e}; }
}
"""

WEEKLY_BODY_HEX = ("0a04081910010a04081910020a04081910040a04081810010a04081810020a040817"
                   "10010a04081710020a04081710030a04081610010a04081610020a04081610030a"
                   "04081610040a04081510010a04081510030a04081410010a04081410020a040814"
                   "10030a04081310030a04081210010a04081110010a04081110030a04081010010a"
                   "04081010020a04081110020a0408131001")

def decode_4001(hex_str):
    b = bytes.fromhex(hex_str)
    i, picks = 0, []
    while i < len(b):
        if b[i] == 0x0a:  # field1 embedded msg
            ln = b[i+1]; sub = b[i+2:i+2+ln]; i += 2+ln
            # sub: 08 <diff varint> 10 <idx varint>
            j = 0; diff = idx = None
            while j < len(sub):
                tag = sub[j]; j += 1
                val = sub[j]; j += 1  # single-byte varints here
                if tag == 0x08: diff = val
                elif tag == 0x10: idx = val
            picks.append((diff, idx))
        else:
            break
    return picks

STATE_JS = r"""
() => {
  const scene = cc.director.getScene();
  const find = (n, name) => {
    if (!n) return null;
    if (n.name === name) return n;
    for (const c of (n.children||[])) { const r = find(c, name); if (r) return r; }
    return null;
  };
  const nv = find(scene, 'NormalView');
  const overlays = nv ? (nv.children||[]).filter(c=>c.active).map(c=>c.name) : [];
  let root = find(scene, 'DoubleChapterEnterView');
  if (!root) return {overlays, onLadder:false};
  const inner = (root.children||[]).find(c => c.name === 'DoubleChapterEnterView');
  if (inner) root = inner;
  const recNode = find(root, 'record');
  const recLabel = recNode ? (recNode.getComponentInChildren ? null : null) : null;
  // grab any label under title/record
  const labels = [];
  const walkL = (n,d)=>{ if(!n||d>4)return; (n._components||[]).forEach(c=>{ if(c&&typeof c.string==='string'&&c.string.trim()) labels.push(c.string); }); (n.children||[]).forEach(k=>walkL(k,d+1)); };
  walkL(find(root,'title'),0); walkL(recNode,0);
  return {overlays, onLadder:true, labels};
}
"""

def get_page(pw):
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    return browser, next(p for ctx in browser.contexts for p in ctx.pages
                         if HOST in (p.url or "") and "/pwa-sw" not in p.url)

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "install"
    pw = sync_playwright().start()
    browser, page = get_page(pw)
    if cmd == "state":
        print("PORT:", PORT, "URL:", page.url)
        print(json.dumps(page.evaluate(STATE_JS), ensure_ascii=False))
    elif cmd == "install":
        print(json.dumps(page.evaluate(INSTALL_JS, sorted(BLOCK)), ensure_ascii=False))
        print("ring cleared, blocklist active, ready")
    elif cmd == "drain":
        rows = page.evaluate(DRAIN_JS)
        print(f"frames: {len(rows)}")
        dump = []
        for r in rows:
            cmd_i = r.get("cmd")
            hexcmd = (hex(cmd_i) if isinstance(cmd_i, int) else cmd_i)
            full = base64.b64decode(r["b64"]).hex() if r.get("b64") else ""
            dump.append({"dir": r["dir"], "cmd": cmd_i, "hexcmd": hexcmd, "len": r["len"], "hex": full})
            print(f"{r['dir']} cmd={cmd_i}({hexcmd}) len={r['len']} {full}")
        with open("tools/_xiaobao_frames.json", "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)
        print("saved -> tools/_xiaobao_frames.json")
    elif cmd == "decode":
        body = sys.argv[2] if len(sys.argv) > 2 else WEEKLY_BODY_HEX
        picks = decode_4001(body)
        print(f"{len(picks)} picks (difficulty, index):")
        from collections import defaultdict
        bydiff = defaultdict(list)
        for diff, idx in picks:
            bydiff[diff].append(idx)
        for diff in sorted(bydiff, reverse=True):
            print(f"  難度 {diff}: index {sorted(bydiff[diff])}")
    elif cmd == "replay":
        page.evaluate(CLEAR_JS)
        r = page.evaluate(REPLAY_JS, {"cmd": 16385, "hex": WEEKLY_BODY_HEX})
        print("send:", r)
        page.wait_for_timeout(900)
        for row in page.evaluate(DRAIN_JS):
            if row.get("cmd") == 16385:
                raw = base64.b64decode(row["b64"]).hex() if row.get("b64") else ""
                print(f"{row['dir']} 0x4001 len={row['len']} {raw}")
    pw.stop()

if __name__ == "__main__":
    main()
