"""Carpark decoration buy+upgrade verification on a live device (CDP).

Subcommands:
  probe-install   wrap netManager._cnet.{send,recv} into a ring buffer (idempotent)
  walk            from the open ParkingDecorateDetailView, step ◀▶ to map each
                  decoration in the current category: name/level/next%/frag/buyable
                  -> find an UNOWNED standard decoration (level 0, not initial)
  buy-upgrade     on the currently-shown decoration: drain baseline, click 購買,
                  drain (capture buy cmd), click 升級 (skin_up 12817), drain
                  (capture upgrade cmd), screenshot + read new level/bonus.

Walk/probe are read-only. buy-upgrade SPENDS 1 fragment (user-authorized).
"""
from __future__ import annotations
import argparse, io, json, sys, time
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
GAME_HOST = "mushroomh5.acenetgame.com"
INITIAL_NAMES = {"拱門", "初始車位", "木柵欄", "普通路燈"}

_INSTALL_JS = r"""
([ringMax]) => {
  if (window.__probe_inst) { window.__probe_ring = []; return 'reset'; }
  const nm = window.netManager; if (!nm || !nm._cnet) return 'no_cnet';
  const sock = nm._cnet;
  if (typeof sock.sendMessage!=='function' || typeof sock.reciveMsg!=='function') return 'no_methods';
  const RING = ringMax|0 || 4000;
  window.__probe_ring=[]; window.__probe_seq=0;
  const toArr=(b)=>{ if(!b)return null; let u; try{
    if(b instanceof Uint8Array)u=b; else if(b.buffer)u=new Uint8Array(b.buffer,b.byteOffset||0,b.byteLength);
    else if(Array.isArray(b))u=Uint8Array.from(b); else return null;}catch(e){return null;}
    const cap=Math.min(u.length,65536), a=new Array(cap); for(let i=0;i<cap;i++)a[i]=u[i]; return a; };
  const push=(cmd,dir,b)=>{ const r=window.__probe_ring;
    r.push({seq:++window.__probe_seq,cmd:cmd|0,dir,ts:Date.now(),
      len:(b?(b.byteLength||b.length||0):0),body:toArr(b)});
    if(r.length>RING)r.splice(0,r.length-RING); };
  const os=sock.sendMessage.bind(sock); sock.sendMessage=function(c,b){try{push(c,'tx',b);}catch(e){}return os(c,b);};
  const orr=sock.reciveMsg.bind(sock); sock.reciveMsg=function(c,b){try{push(c,'rx',b);}catch(e){}return orr(c,b);};
  window.__probe_inst=true; return 'installed';
}
"""
_DRAIN_JS = r"() => { const b=window.__probe_ring||[]; window.__probe_ring=[]; return b; }"
_CLEAR_JS = r"() => { window.__probe_ring=[]; return 1; }"

_CLICK_JS = r"""
([path]) => {
  const scene=cc.director.getScene();
  const find=(r,parts)=>{let n=r;for(const p of parts){if(!n||!n.children)return null;
    n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
  const node=find(scene, path.split('/').filter(Boolean));
  if(!node) return {ok:false,err:'not_found',path};
  try{ node.emit('click',node); return {ok:true,active:!!node.active}; }catch(e){return {ok:false,err:String(e)};}
}
"""

_READ_DETAIL_JS = r"""
([]) => {
  const scene=cc.director.getScene();
  let root=null; const st=[scene];
  while(st.length){const n=st.pop(); if(n&&n.name==='ParkingDecorateDetailView'){root=n;break;}
    if(n&&n.children) for(const c of n.children) st.push(c);}
  if(!root) return {err:'no_detail'};
  const find=(r,parts)=>{let n=r;for(const p of parts){if(!n||!n.children)return null;
    n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
  const lab=(n)=>{ if(!n)return null; for(const c of (n._components||[])) if(c&&typeof c.string==='string')return c.string; return null;};
  const out={};
  out.name=lab(find(root,['root','nodeShow','txtName']));
  out.cur=lab(find(root,['root','nodeChange','ScrollView','view','content','0','txtBase']));
  out.next=lab(find(root,['root','nodeChange','ScrollView','view','content','0','txtNext']));
  out.frag=lab(find(root,['root','item','txtNext']));
  out.limit=lab(find(root,['root','item','txtLimit']));
  out.buy_cost=lab(find(root,['root','btnBuy','num']));
  out.top_label=lab(find(root,['root','nodeTop','txtLabel']));
  const bB=find(root,['root','btnBuy']); out.buy_active=bB?bB.active:null;
  const bU=find(root,['root','btnUnlock']); out.up_active=bU?bU.active:null;
  const starContent=find(root,['root','nodeShow','ScrollView','view','content']);
  let lvl=0; const PIP={one:1,two:2,three:3};
  if(starContent) for(const slot of (starContent.children||[]))
    for(const f of (slot.children||[])) if(f.active&&PIP[f.name]!=null) lvl+=PIP[f.name];
  out.level=lvl;
  return out;
}
"""

DETAIL = "/UIRoot/NormalView/ParkingDecorateDetailView/root"

def attach(port):
    from playwright.sync_api import sync_playwright
    pw=sync_playwright().start(); b=pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    for ctx in b.contexts:
        for p in ctx.pages:
            if GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url: return pw,p
    pw.stop(); raise SystemExit("no page")

def fmt(e, n=24):
    body=e.get("body") or []; hexs=" ".join(f"{x:02x}" for x in body[:n])
    return f"#{e['seq']:>4} {e['dir']} cmd=0x{e['cmd']:04x}({e['cmd']}) len={e['len']:>4} {hexs}{'...' if len(body)>n else ''}"

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    for s in ("probe-install","walk","buy-upgrade"):
        sp=sub.add_parser(s); sp.add_argument("--port",type=int,default=9223)
    ap_walk=[a for a in sub.choices.values()]
    sub.choices["walk"].add_argument("--steps",type=int,default=14)
    sub.choices["walk"].add_argument("--category",type=int,default=-1,
        help="0=大門 1=場景 2=圍欄 3=路燈 4=裝飾; if set, select tab + open a cell first")
    sub.choices["walk"].add_argument("--cell-idx",type=int,default=1,
        help="grid cell index to open before walking via btnRight (default 1; 0 is the initial item)")
    sub.choices["buy-upgrade"].add_argument("--shot",default="logs/_scratch/verify_upgrade.png")
    sub.choices["buy-upgrade"].add_argument("--do-upgrade",action="store_true",
        help="also click 升級 after 購買 (default: only buy 1 fragment)")
    a=ap.parse_args()
    pw,page=attach(a.port)

    if a.cmd=="probe-install":
        print(page.evaluate(_INSTALL_JS,[4000])); pw.stop(); return

    if a.cmd=="walk":
        if a.category>=0:
            # close any sticky detail popup first so the next cell click opens fresh
            print(f"[close] {page.evaluate(_CLICK_JS,[DETAIL+'/btnClose'])}"); time.sleep(1.0)
            tab=f"/UIRoot/NormalView/ParkingDecorateView/root/ScrollView-001/view/content/{a.category}"
            print(f"[tab] {page.evaluate(_CLICK_JS,[tab])}"); time.sleep(1.4)
            cell=f"/UIRoot/NormalView/ParkingDecorateView/root/ScrollView/view/content/{a.cell_idx}"
            print(f"[cell{a.cell_idx}] {page.evaluate(_CLICK_JS,[cell])}"); time.sleep(1.4)
        seen=[]
        d=page.evaluate(_READ_DETAIL_JS,[])
        for i in range(a.steps):
            d=page.evaluate(_READ_DETAIL_JS,[])
            if d.get("err"): print("detail not open"); break
            nm=d.get("name")
            unowned = (d.get("level")==0 and nm not in INITIAL_NAMES)
            mark=" <== UNOWNED candidate" if unowned else ""
            print(f"  {nm!r:18} lv={d.get('level')} cur={d.get('cur')} next={d.get('next')} "
                  f"frag={d.get('frag')} limit={d.get('limit')} buy={d.get('buy_cost')} "
                  f"buyBtn={d.get('buy_active')} upBtn={d.get('up_active')}{mark}")
            if nm in [s['name'] for s in seen]: print("  (wrapped)"); break
            seen.append({'name':nm,'unowned':unowned})
            page.evaluate(_CLICK_JS,[DETAIL+"/nodeShow/btnRight"]); time.sleep(1.0)
        pw.stop(); return

    if a.cmd=="buy-upgrade":
        before=page.evaluate(_READ_DETAIL_JS,[])
        print(f"[before] {json.dumps(before,ensure_ascii=False)}")
        page.evaluate(_CLEAR_JS); time.sleep(0.5); page.evaluate(_DRAIN_JS)
        # 購買 one fragment
        print(f"[buy] click 購買: {page.evaluate(_CLICK_JS,[DETAIL+'/btnBuy'])}")
        time.sleep(2.0)
        buy_frames=page.evaluate(_DRAIN_JS) or []
        print(f"=== WS after 購買 ({len(buy_frames)} frames) ===")
        for e in buy_frames: print("  "+fmt(e))
        if a.do_upgrade:
            time.sleep(0.5); page.evaluate(_DRAIN_JS)
            print(f"[upgrade] click 升級: {page.evaluate(_CLICK_JS,[DETAIL+'/btnUnlock'])}")
            time.sleep(2.0)
            up_frames=page.evaluate(_DRAIN_JS) or []
            print(f"=== WS after 升級 ({len(up_frames)} frames) ===")
            for e in up_frames: print("  "+fmt(e))
        time.sleep(0.8)
        after=page.evaluate(_READ_DETAIL_JS,[])
        print(f"[after] {json.dumps(after,ensure_ascii=False)}")
        try: page.screenshot(path=a.shot); print(f"[shot] {a.shot}")
        except Exception as e: print(f"[shot] fail {e}")
        pw.stop(); return

if __name__=="__main__":
    main()
