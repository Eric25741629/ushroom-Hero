"""Local web viewer for ADB-captured game login tokens.

Reads every auth_state/_auth_capture_*.json (written by tools/adb_token_login.py),
shows each account's WS-login ticket in the browser with copy buttons, and lets
you self-test: 測試登入 (live WS role_login, code==0?) and 重新擷取 (re-mint a
fresh ticket by restarting that device/user's app and re-scraping logcat).

SECURITY: this page shows live login tickets in plaintext. It binds to 127.0.0.1
only by default. Do not expose it on a public interface.

Run (from repo root):
  python tools/token_viewer.py            # http://127.0.0.1:5099
  python tools/token_viewer.py --port 5099
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adb_token_login as atl  # noqa: E402

AUTH_DIR = Path("auth_state")
CAPTURE_GLOB = "_auth_capture_*.json"
app = Flask(__name__)


def _capture_files() -> list[Path]:
    return sorted(AUTH_DIR.glob(CAPTURE_GLOB))


def _safe_file(name: str) -> Path:
    """Resolve a capture filename inside auth_state/, rejecting traversal."""
    p = (AUTH_DIR / Path(name).name)
    if p.suffix != ".json" or not p.name.startswith("_auth_capture_"):
        raise ValueError("invalid file")
    return p


def _load(p: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    creds = obj.get("creds")
    return creds if isinstance(creds, dict) else None


@app.get("/api/creds")
def api_creds() -> Any:
    out = []
    for p in _capture_files():
        creds = _load(p)
        if creds:
            out.append({"file": p.name, "creds": creds})
    return jsonify(out)


@app.get("/api/devices")
def api_devices() -> Any:
    try:
        raw = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                             timeout=15).stdout
    except Exception as exc:  # noqa: BLE001
        return jsonify({"devices": [], "error": str(exc)})
    devices = [ln.split("\t")[0] for ln in raw.splitlines()[1:]
               if "\tdevice" in ln]
    return jsonify({"devices": devices})


@app.post("/api/verify")
def api_verify() -> Any:
    name = (request.json or {}).get("file", "")
    try:
        creds = _load(_safe_file(name))
    except ValueError:
        return jsonify({"ok": False, "error": "bad file"}), 400
    if not creds:
        return jsonify({"ok": False, "error": "creds not found"}), 404
    try:
        result = atl.verify_ws(creds)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return jsonify(result)


@app.post("/api/recapture")
def api_recapture() -> Any:
    name = (request.json or {}).get("file", "")
    try:
        path = _safe_file(name)
    except ValueError:
        return jsonify({"ok": False, "error": "bad file"}), 400
    creds = _load(path)
    if not creds:
        return jsonify({"ok": False, "error": "creds not found"}), 404
    device = creds.get("device_name") or ""
    user = creds.get("_user")
    return _do_capture(device, user, path)


@app.post("/api/capture_new")
def api_capture_new() -> Any:
    body = request.json or {}
    device = (body.get("device") or "").strip()
    user_raw = body.get("user")
    if not device:
        return jsonify({"ok": False, "error": "device required"}), 400
    user = int(user_raw) if str(user_raw).strip() not in ("", "None") else None
    suffix = f"_u{user}" if user is not None else ""
    short = device.split(".")[0].replace(":", "-")
    path = AUTH_DIR / f"_auth_capture_{short}{suffix}.json"
    return _do_capture(device, user, path)


def _do_capture(device: str, user: int | None, path: Path) -> Any:
    if not device:
        return jsonify({"ok": False, "error": "no device on record"}), 400
    try:
        atl.restart_app(device, user)
        found = atl.scrape(device, 120)
        if "auth_reply" not in found:
            return jsonify({"ok": False,
                            "error": "no login_auth reply in logcat (slow boot? "
                                     "release build with logging off?)"}), 502
        creds = atl.build_creds(found, device)
        creds["_user"] = user
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"creds": creds}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return jsonify({"ok": True, "file": path.name, "creds": creds})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


@app.get("/")
def index() -> str:
    return PAGE


PAGE = r"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>菇勇者 Token Viewer</title>
<style>
  :root{--bg:#0f1115;--card:#181b22;--line:#262b35;--ink:#e7ebf0;--mut:#8b94a3;
        --accent:#5db0ff;--ok:#3ad17a;--bad:#ff6b6b;--warn:#ffb454;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 ui-monospace,"Cascadia Code",Consolas,monospace}
  header{padding:18px 24px;border-bottom:1px solid var(--line);
         display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
  h1{font-size:18px;margin:0;font-family:system-ui,sans-serif}
  .sub{color:var(--mut);font-size:12px}
  .wrap{padding:20px 24px;display:grid;gap:18px;
        grid-template-columns:repeat(auto-fill,minmax(440px,1fr))}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:16px 18px;display:flex;flex-direction:column;gap:10px}
  .acct{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .acct b{font-family:system-ui,sans-serif;font-size:15px}
  .badge{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);
         color:var(--mut)}
  .badge.src{color:var(--accent);border-color:#2b4a66}
  .badge.user{color:var(--warn);border-color:#5a4220}
  table{width:100%;border-collapse:collapse}
  td{padding:3px 6px;vertical-align:top;border-bottom:1px solid #1f242d}
  td.k{color:var(--mut);width:118px;white-space:nowrap}
  td.v{word-break:break-all}
  .row-copy{cursor:pointer;color:var(--mut);user-select:none;padding-left:6px}
  .row-copy:hover{color:var(--accent)}
  textarea{width:100%;height:120px;background:#0c0e12;color:var(--ink);
           border:1px solid var(--line);border-radius:8px;padding:8px;resize:vertical;
           font:12px/1.45 ui-monospace,monospace}
  .btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
  button{background:#222834;color:var(--ink);border:1px solid var(--line);
         border-radius:8px;padding:7px 12px;cursor:pointer;font:13px system-ui}
  button:hover{border-color:var(--accent)}
  button.primary{background:#1c3550;border-color:#2b4a66}
  button:disabled{opacity:.5;cursor:wait}
  .result{font-size:12px;min-height:18px}
  .result.ok{color:var(--ok)} .result.bad{color:var(--bad)} .result.run{color:var(--warn)}
  .note{color:var(--mut);font-size:12px;padding:0 24px 8px}
  .newbar{padding:14px 24px;border-top:1px solid var(--line);display:flex;
          gap:10px;align-items:center;flex-wrap:wrap}
  select,input{background:#0c0e12;color:var(--ink);border:1px solid var(--line);
               border-radius:8px;padding:7px 10px;font:13px ui-monospace,monospace}
  .copyhint{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
            background:#1c3550;border:1px solid #2b4a66;padding:8px 16px;border-radius:8px;
            opacity:0;transition:opacity .2s;pointer-events:none}
  .copyhint.show{opacity:1}
</style></head>
<body>
<header>
  <h1>菇勇者 Token Viewer</h1>
  <span class="sub">ADB logcat 擷取的登入憑證 · 本機 127.0.0.1 · token 為明文，勿外流</span>
  <button onclick="load()" style="margin-left:auto">↻ 重新整理</button>
</header>
<div class="note">⚠ 「測試登入」會送真實 WS 登入，會<b>踢掉該帳號當前遊戲 session</b>（異地登入）。「重新擷取」會重啟該裝置/分身的 App 換一張新 ticket（約 10–30 秒）。</div>
<div class="wrap" id="cards"></div>
<div class="newbar">
  <b style="font-family:system-ui">擷取新的：</b>
  <select id="newDev"><option value="">(讀取裝置中…)</option></select>
  <input id="newUser" placeholder="user id（雙開填 999，一般留空）" size="26">
  <button class="primary" onclick="captureNew()">＋ 擷取</button>
  <span class="result" id="newRes"></span>
</div>
<div class="copyhint" id="hint">已複製</div>
<script>
const FIELDS=[["loginTicket","ticket"],["pKey","p_key"],["roleId","role_id"],
  ["loginTime","time"],["loginGameId","game_id"],["isWhiteIp","white_ip"],
  ["ip","ip"],["_ws_url","ws_url"]];
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function hint(t){const h=document.getElementById('hint');h.textContent=t;h.classList.add('show');
  setTimeout(()=>h.classList.remove('show'),1100);}
function copy(t){navigator.clipboard.writeText(t).then(()=>hint('已複製'));}
function when(ts){if(!ts)return'';const d=new Date(ts*1000);
  return d.toLocaleString('zh-TW',{hour12:false});}

async function load(){
  const r=await fetch('/api/creds'); const list=await r.json();
  const box=document.getElementById('cards'); box.innerHTML='';
  if(!list.length){box.innerHTML='<div class="sub">尚無擷取檔。下方可擷取新的。</div>';}
  for(const it of list) box.appendChild(card(it));
  loadDevices();
}
function card(it){
  const c=it.creds, el=document.createElement('div'); el.className='card';
  const userBadge=(c._user!=null)?`<span class="badge user">user ${c._user}</span>`:'';
  let rows='';
  for(const [k,label] of FIELDS){ if(c[k]==null)continue;
    rows+=`<tr><td class="k">${label}</td><td class="v">${esc(c[k])}`
        +`<span class="row-copy" title="複製" data-c="${esc(c[k])}">⧉</span></td></tr>`;}
  const full=JSON.stringify({creds:c},null,2);
  el.innerHTML=`
    <div class="acct"><b>${esc(c.uname||'?')}</b>
      <span class="badge">uid ${esc(c.uid||'')}</span>
      ${userBadge}
      <span class="badge src">${esc((c._source||'')+' · '+(c.device_name||'').split('.')[0])}</span>
    </div>
    <div class="sub">擷取於 ${when(c._captured_at)}　檔案 ${esc(it.file)}</div>
    <table>${rows}</table>
    <textarea readonly>${esc(full)}</textarea>
    <div class="btns">
      <button class="primary" data-act="verify">測試登入</button>
      <button data-act="recap">重新擷取</button>
      <button data-act="copyjson">複製整包 JSON</button>
      <span class="result" data-res></span>
    </div>`;
  el.querySelectorAll('.row-copy').forEach(s=>s.onclick=()=>copy(s.dataset.c));
  const res=el.querySelector('[data-res]');
  el.querySelector('[data-act=copyjson]').onclick=()=>copy(full);
  el.querySelector('[data-act=verify]').onclick=e=>verify(e.target,it.file,res);
  el.querySelector('[data-act=recap]').onclick=e=>recap(e.target,it.file,res);
  return el;
}
async function verify(btn,file,res){
  btn.disabled=true; res.className='result run'; res.textContent='登入中…（會踢 session）';
  try{
    const r=await fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file})}); const j=await r.json();
    if(j.ok){res.className='result ok';res.textContent=`✓ SUCCESS code=${j.code} role_id=${j.role_id}`;}
    else{res.className='result bad';res.textContent=`✗ FAIL ${j.error||('code='+j.code)}`;}
  }catch(e){res.className='result bad';res.textContent='✗ '+e;}
  btn.disabled=false;
}
async function recap(btn,file,res){
  btn.disabled=true; res.className='result run'; res.textContent='重啟 App 擷取中…';
  try{
    const r=await fetch('/api/recapture',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({file})}); const j=await r.json();
    if(j.ok){res.className='result ok';res.textContent='✓ 已換新 ticket';load();}
    else{res.className='result bad';res.textContent='✗ '+(j.error||'fail');}
  }catch(e){res.className='result bad';res.textContent='✗ '+e;}
  btn.disabled=false;
}
async function loadDevices(){
  const r=await fetch('/api/devices'); const j=await r.json();
  const sel=document.getElementById('newDev');
  sel.innerHTML='<option value="">選擇裝置…</option>'
    +(j.devices||[]).map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join('');
}
async function captureNew(){
  const device=document.getElementById('newDev').value;
  const user=document.getElementById('newUser').value.trim();
  const res=document.getElementById('newRes');
  if(!device){res.className='result bad';res.textContent='✗ 請選裝置';return;}
  res.className='result run';res.textContent='重啟 App 擷取中…';
  try{
    const r=await fetch('/api/capture_new',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({device,user})}); const j=await r.json();
    if(j.ok){res.className='result ok';res.textContent='✓ '+j.file;load();}
    else{res.className='result bad';res.textContent='✗ '+(j.error||'fail');}
  }catch(e){res.className='result bad';res.textContent='✗ '+e;}
}
load();
</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5099)
    args = ap.parse_args()
    print(f"[token_viewer] http://{args.host}:{args.port}  "
          f"(serving {len(_capture_files())} capture file(s))", flush=True)
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
