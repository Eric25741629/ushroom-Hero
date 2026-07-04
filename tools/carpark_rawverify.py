"""Carpark decoration survey + buy/upgrade verification over RAW CDP (reliable).

Uses tools/rawcdp.RawCDP (single-page ws, Runtime.enable) — avoids Playwright's
stalling multi-target attach. Subcommands:
  survey       per category: grid cell count + each cell's lock/owned + name/level
  buy-upgrade  on a target cell: drain WS baseline, 購買 1 frag (capture cmd),
               升級 (capture skin_up 12817), verify level + bonus delta, screenshot
"""
from __future__ import annotations
import argparse, base64, io, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rawcdp import RawCDP  # also sets sys.stdout to a UTF-8 wrapper

DV = "/UIRoot/NormalView/ParkingDecorateView"
DETAIL = "/UIRoot/NormalView/ParkingDecorateDetailView/root"
GRID = DV + "/root/ScrollView/view/content"
TABS = DV + "/root/ScrollView-001/view/content"
CATS = ["大門", "場景", "圍欄", "路燈", "裝飾"]

_CLICK = r"""
(path) => { const s=cc.director.getScene();
  const f=(r,ps)=>{let n=r;for(const p of ps){if(!n||!n.children)return null;
    n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
  const n=f(s,path.split('/').filter(Boolean)); if(!n)return{ok:false};
  try{n.emit('click',n);return{ok:true,active:!!n.active};}catch(e){return{ok:false,err:String(e)};} }
"""
_GRID_READ = r"""
(gp) => { const s=cc.director.getScene();
  const f=(r,ps)=>{let n=r;for(const p of ps){if(!n||!n.children)return null;
    n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
  const g=f(s,gp.split('/').filter(Boolean)); if(!g)return{err:'no_grid'};
  const kid=(c,nm)=>{const k=(c.children||[]).find(x=>x.name===nm);return k?!!k.active:null;};
  return {count:g.children.length, cells:g.children.map((c,i)=>({i,lock:kid(c,'lock'),sel:kid(c,'sel'),rp:kid(c,'rp')}))}; }
"""
_DETAIL = r"""
(x) => { const s=cc.director.getScene(); let r=null; const st=[s];
  while(st.length){const n=st.pop(); if(n&&n.name==='ParkingDecorateDetailView'){r=n;break;}
    if(n&&n.children)for(const c of n.children)st.push(c);}
  if(!r)return{open:false};
  const f=(rt,ps)=>{let n=rt;for(const p of ps){if(!n||!n.children)return null;
    n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
  const lab=(n)=>{if(!n)return null;for(const c of (n._components||[]))if(c&&typeof c.string==='string')return c.string;return null;};
  let lvl=0;const PIP={one:1,two:2,three:3};
  const sc=f(r,['root','nodeShow','ScrollView','view','content']);
  if(sc)for(const slot of (sc.children||[]))for(const x of (slot.children||[]))if(x.active&&PIP[x.name]!=null)lvl+=PIP[x.name];
  const bB=f(r,['root','btnBuy']),bU=f(r,['root','btnUnlock']);
  const nodeItem=f(r,['root','nodeItem']);
  return {open:true, name:lab(f(r,['root','nodeShow','txtName'])), level:lvl,
    cur:lab(f(r,['root','nodeChange','ScrollView','view','content','0','txtBase'])),
    next:lab(f(r,['root','nodeChange','ScrollView','view','content','0','txtNext'])),
    frag:lab(f(r,['root','item','txtNext'])), limit:lab(f(r,['root','item','txtLimit'])),
    buy_cost:lab(f(r,['root','btnBuy','num'])), balance:lab(nodeItem),
    buy_active:bB?!!bB.active:null, up_active:bU?!!bU.active:null}; }
"""
_INSTALL = r"""
(ringMax) => {
  if (window.__probe_inst) { window.__probe_ring=[]; return 'reset'; }
  const nm=window.netManager; if(!nm||!nm._cnet) return 'no_cnet';
  const sock=nm._cnet;
  if(typeof sock.sendMessage!=='function'||typeof sock.reciveMsg!=='function') return 'no_methods';
  const RING=ringMax|0||4000; window.__probe_ring=[]; window.__probe_seq=0;
  const toArr=(b)=>{ if(!b)return null; let u; try{
    if(b instanceof Uint8Array)u=b; else if(b.buffer)u=new Uint8Array(b.buffer,b.byteOffset||0,b.byteLength);
    else if(Array.isArray(b))u=Uint8Array.from(b); else return null;}catch(e){return null;}
    const cap=Math.min(u.length,65536),a=new Array(cap);for(let i=0;i<cap;i++)a[i]=u[i];return a;};
  const push=(cmd,dir,b)=>{const r=window.__probe_ring;
    r.push({seq:++window.__probe_seq,cmd:cmd|0,dir,ts:Date.now(),len:(b?(b.byteLength||b.length||0):0),body:toArr(b)});
    if(r.length>RING)r.splice(0,r.length-RING);};
  const os=sock.sendMessage.bind(sock);sock.sendMessage=function(c,b){try{push(c,'tx',b);}catch(e){}return os(c,b);};
  const orr=sock.reciveMsg.bind(sock);sock.reciveMsg=function(c,b){try{push(c,'rx',b);}catch(e){}return orr(c,b);};
  window.__probe_inst=true; return 'installed';
}
"""
_DRAIN = r"(x) => { const b=window.__probe_ring||[]; window.__probe_ring=[]; return b; }"

# Robust click of a node found by NAME within an active view subtree. Tries
# emit('click') then the cc.Button clickEvents fallback (some dialogs bind via
# editor clickEvents, where emit no-ops). Returns {ok, method} or {ok:false}.
_CLICK_IN = r"""
(args) => { const [viewName, nodeName] = args; const s=cc.director.getScene();
  const findView=(nm)=>{const st=[s];while(st.length){const n=st.pop();
    if(n&&n.name===nm&&n.active)return n; if(n&&n.children)for(const c of n.children)st.push(c);}return null;};
  const root=findView(viewName); if(!root)return{ok:false,err:'view'};
  const findNode=(r,nm)=>{const st=[r];while(st.length){const n=st.pop();
    if(n&&n.name===nm)return n; if(n&&n.children)for(const c of n.children)st.push(c);}return null;};
  const node=findNode(root,nodeName); if(!node)return{ok:false,err:'node'};
  try{ if(node.emit) node.emit('click',node); }catch(e){}
  let fired='emit';
  try{ const btn=node.getComponent && node.getComponent('cc.Button');
    if(btn && btn.clickEvents && btn.clickEvents.length){
      for(const ev of btn.clickEvents){ const t=ev.target, cn=ev._componentName||ev.component, h=ev.handler;
        const comp=t&&t.getComponent&&t.getComponent(cn);
        if(comp&&typeof comp[h]==='function'){ comp[h](null, ev.customEventData); fired='clickEvents'; } } }
  }catch(e){}
  return {ok:true, method:fired}; }
"""
# Read first label string of a node found by NAME within a view subtree.
_READ_IN = r"""
(args) => { const [viewName, nodeName] = args; const s=cc.director.getScene();
  const findView=(nm)=>{const st=[s];while(st.length){const n=st.pop();
    if(n&&n.name===nm&&n.active)return n; if(n&&n.children)for(const c of n.children)st.push(c);}return null;};
  const root=findView(viewName); if(!root)return null;
  const findNode=(r,nm)=>{const st=[r];while(st.length){const n=st.pop();
    if(n&&n.name===nm)return n; if(n&&n.children)for(const c of n.children)st.push(c);}return null;};
  const node=findNode(root,nodeName); if(!node)return null;
  for(const c of (node._components||[])) if(c&&typeof c.string==='string') return c.string; return null; }
"""
_CLEAR = r"(x) => { window.__probe_ring=[]; return (window.__probe_ring||[]).length; }"
_PROBE_OK = r"(x) => ({probe: !!window.__probe_inst, ring: (window.__probe_ring||[]).length, cc: (typeof cc!=='undefined'), nm: (typeof netManager!=='undefined')})"


def click(c, path): return c.call(_CLICK, [path]) if False else c.evaluate(f"({_CLICK})({json.dumps(path)})")
def detail(c): return c.evaluate(f"({_DETAIL})(0)")
def grid(c): return c.evaluate(f"({_GRID_READ})({json.dumps(GRID)})")


def close_detail(c):
    if detail(c).get("open"):
        c.evaluate(f"({_CLICK})({json.dumps(DETAIL + '/btnClose')})"); time.sleep(1.0)


def fmt(e, n=28):
    body = e.get("body") or []
    hexs = " ".join(f"{x:02x}" for x in body[:n])
    return f"#{e.get('seq')} {e.get('dir')} cmd=0x{e.get('cmd'):04x}({e.get('cmd')}) len={e.get('len')} {hexs}{'...' if len(body)>n else ''}"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("survey"); sp.add_argument("--port", type=int, default=9223)
    pp = sub.add_parser("probe-install"); pp.add_argument("--port", type=int, default=9223)
    wp = sub.add_parser("walk"); wp.add_argument("--port", type=int, default=9223)
    wp.add_argument("--category", type=int, default=0); wp.add_argument("--cell", type=int, default=4)
    wp.add_argument("--steps", type=int, default=12)
    shp = sub.add_parser("shop"); shp.add_argument("--port", type=int, default=9223)
    shp.add_argument("--shot", default="logs/_scratch/carpark_shop.png")
    tp = sub.add_parser("tree"); tp.add_argument("--port", type=int, default=9223)
    tp.add_argument("--view", required=True); tp.add_argument("--depth", type=int, default=5)
    fb = sub.add_parser("full-buy"); fb.add_argument("--port", type=int, default=9223)
    fb.add_argument("--category", type=int, required=True); fb.add_argument("--cell", type=int, required=True)
    fb.add_argument("--qty", type=int, required=True, help="fragments to buy via the Mall dialog")
    fb.add_argument("--dry", action="store_true", help="set qty + read price but DO NOT confirm purchase")
    fb.add_argument("--do-upgrade", action="store_true")
    fb.add_argument("--shot", default="logs/_scratch/verify_full.png")
    bp = sub.add_parser("buy-upgrade"); bp.add_argument("--port", type=int, default=9223)
    bp.add_argument("--category", type=int, required=True)
    bp.add_argument("--cell", type=int, required=True)
    bp.add_argument("--buy-times", type=int, default=1, help="how many 購買 clicks (1 fragment each)")
    bp.add_argument("--do-upgrade", action="store_true")
    bp.add_argument("--shot", default="logs/_scratch/verify_upgrade.png")
    a = ap.parse_args()

    c = RawCDP(a.port, timeout=20.0); c.enable_runtime()
    print("[probe]", json.dumps(c.evaluate(f"({_PROBE_OK})(0)"), ensure_ascii=False))

    if a.cmd == "survey":
        for ci, cat in enumerate(CATS):
            close_detail(c)
            c.evaluate(f"({_CLICK})({json.dumps(f'{TABS}/{ci}')})"); time.sleep(1.4)
            g = grid(c)
            print(f"\n=== {cat} (cat {ci}) grid count={g.get('count')} ===")
            for cell in g.get("cells", []):
                c.evaluate(f"({_CLICK})({json.dumps(f'{GRID}/' + str(cell['i']))})"); time.sleep(0.9)
                d = detail(c)
                owned = "OWNED" if (d.get("level", 0) or 0) >= 1 else ("LOCKED?" if cell.get("lock") else "lv0")
                print(f"  cell{cell['i']} lock={cell.get('lock')} -> {d.get('name')!r} lv={d.get('level')} "
                      f"frag={d.get('frag')} buy={d.get('buy_cost')} buyBtn={d.get('buy_active')} "
                      f"upBtn={d.get('up_active')} [{owned}]")
                close_detail(c)
        c.close(); return

    if a.cmd == "probe-install":
        print("[install]", c.evaluate(f"({_INSTALL})(4000)")); c.close(); return

    if a.cmd == "tree":
        treejs = r"""(args)=>{const [vn,maxD]=args; const s=cc.director.getScene();
          let root=null; const st=[s]; while(st.length){const n=st.pop();
            if(n&&n.name===vn){root=n;break;} if(n&&n.children)for(const k of n.children)st.push(k);}
          if(!root)return{err:'not_found'};
          const lab=(n)=>{const o=[];for(const cc2 of (n._components||[]))if(cc2&&typeof cc2.string==='string'&&cc2.string.trim())o.push(cc2.string);return o;};
          const dump=(n,d)=>({name:n.name,active:n.active,labels:lab(n),
            kids:d<maxD&&n.children?n.children.map(k=>dump(k,d+1)):((n.children||[]).length||undefined)});
          return dump(root,0);}"""
        out = c.evaluate(f"({treejs})({json.dumps([a.view, a.depth])})")
        print(json.dumps(out, ensure_ascii=False, indent=1)); c.close(); return

    if a.cmd == "shop":
        close_detail(c)
        # close decorate view, open 車友商行 shop via ParkingMainView bottom/btnShop
        c.evaluate(f"({_CLICK})({json.dumps(DV + '/root/btnClose')})"); time.sleep(0.8)
        c.evaluate(f"({_CLICK})({json.dumps('/UIRoot/NormalView/ParkingMainView/bottom/btnShop')})"); time.sleep(2.2)
        # list overlay views active under NormalView + dump labels of any shop-ish view
        listjs = r"""(x)=>{const s=cc.director.getScene();
          const f=(r,ps)=>{let n=r;for(const p of ps){if(!n||!n.children)return null;
            n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
          const nv=f(s,['UIRoot','NormalView']); const out={active:[],labels:[]};
          if(nv)for(const c of nv.children) if(c.active) out.active.push(c.name);
          // dump labels under any node whose name includes Shop/shop
          const walk=(n,path,d)=>{ if(!n||d>16||!n.active)return;
            for(const cc2 of (n._components||[])) if(cc2&&typeof cc2.string==='string'&&cc2.string.trim())
              out.labels.push({p:path+'/'+(n.name||'?'),t:cc2.string});
            for(const k of (n.children||[])) walk(k,path+'/'+(n.name||'?'),d+1); };
          if(nv)for(const c of nv.children) if(c.active && /shop|Shop|Store/.test(c.name)) walk(c,c.name,0);
          return out; }"""
        info = c.evaluate(listjs)
        print("[shop] active overlays:", json.dumps(info.get("active"), ensure_ascii=False))
        print("[shop] labels:")
        for l in info.get("labels", [])[:60]:
            print(f"   {l['t']!r:26} {l['p']}")
        try:
            c._send("Page.enable"); res = c._send("Page.captureScreenshot", {"format": "png"})
            with open(a.shot, "wb") as f: f.write(base64.b64decode(res["data"]))
            print(f"[shot] {a.shot}")
        except Exception as e: print(f"[shot] fail {e}")
        c.close(); return

    if a.cmd == "walk":
        close_detail(c)
        c.evaluate(f"({_CLICK})({json.dumps(f'{TABS}/{a.category}')})"); time.sleep(1.4)
        c.evaluate(f"({_CLICK})({json.dumps(f'{GRID}/' + str(a.cell))})"); time.sleep(1.4)
        right = json.dumps(DETAIL + "/nodeShow/btnRight")
        seen = []
        for i in range(a.steps):
            d = detail(c)
            if not d.get("open"): print("  (detail closed)"); break
            nm = d.get("name")
            unowned = (d.get("level", 0) or 0) == 0
            print(f"  {nm!r:18} lv={d.get('level')} cur={d.get('cur')} next={d.get('next')} "
                  f"frag={d.get('frag')} limit={d.get('limit')} buy={d.get('buy_cost')} "
                  f"buyBtn={d.get('buy_active')} upBtn={d.get('up_active')}"
                  f"{'  <== lv0/unowned' if unowned else ''}")
            if nm in seen: print("  (wrapped)"); break
            seen.append(nm)
            c.evaluate(f"({_CLICK})({right})"); time.sleep(1.1)
        c.close(); return

    if a.cmd == "full-buy":
        MALL = "/UIRoot/NormalView/MallTipsView/MallTipsView"
        view_open = r"""(vn)=>{const s=cc.director.getScene();const st=[s];
          while(st.length){const n=st.pop();if(n&&n.name===vn&&n.active)return true;
            if(n&&n.children)for(const k of n.children)st.push(k);}return false;}"""
        lab_js = r"""(path)=>{const s=cc.director.getScene();const f=(r,ps)=>{let n=r;
          for(const p of ps){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
          const n=f(s,path.split('/').filter(Boolean));if(!n)return null;
          for(const c of (n._components||[]))if(c&&typeof c.string==='string')return c.string;return null;}"""
        if not c.evaluate(f"({view_open})({json.dumps('ParkingDecorateView')})"):
            print("[nav] reopen panel via btnSkin")
            c.evaluate(f"({_CLICK})({json.dumps('/UIRoot/NormalView/ParkingMainView/bottom/btnSkin')})"); time.sleep(2.2)
        close_detail(c)
        c.evaluate(f"({_CLICK})({json.dumps(f'{TABS}/{a.category}')})"); time.sleep(1.4)
        c.evaluate(f"({_CLICK})({json.dumps(f'{GRID}/' + str(a.cell))})"); time.sleep(1.4)
        before = detail(c)
        print(f"[before] {json.dumps(before, ensure_ascii=False)}")
        if not before.get("open"): print("ERR detail not open"); c.close(); return
        # open Mall buy dialog
        c.evaluate(f"({_CLICK})({json.dumps(DETAIL + '/btnBuy')})"); time.sleep(1.8)
        if not c.evaluate(f"({view_open})({json.dumps('MallTipsView')})"):
            print("ERR MallTipsView not open"); c.close(); return
        def click_in(view, node):
            return c.evaluate(f"({_CLICK_IN})({json.dumps([view, node])})")
        def read_in(view, node):
            return c.evaluate(f"({_READ_IN})({json.dumps([view, node])})")
        # raise qty to a.qty (starts at 1) via btnAdd (robust: emit + clickEvents)
        r = {"method": "n/a"}
        for _ in range(max(0, a.qty - 1)):
            r = click_in("MallTipsView", "btnAdd"); time.sleep(0.3)
        qty = read_in("MallTipsView", "EditBox")
        price = read_in("MallTipsView", "price")
        print(f"[dialog] qty={qty} price={price} (btnAdd via {r.get('method')})")
        if a.dry:
            print("[dry] not confirming purchase; closing"); c.close(); return
        if str(qty) != str(a.qty):
            print(f"[abort] qty={qty} != requested {a.qty}; not confirming to avoid wrong spend")
            c.close(); return
        # confirm purchase -> capture mall buy cmd
        c.evaluate(f"({_CLEAR})(0)"); time.sleep(0.3); c.evaluate(f"({_DRAIN})(0)")
        print(f"[confirm-buy] {click_in('MallTipsView', 'btnBuy')}")
        time.sleep(2.2)
        buyf = c.evaluate(f"({_DRAIN})(0)") or []
        print(f"=== WS after Mall 購買 confirm ({len(buyf)}) ===")
        for e in buyf: print("  " + fmt(e))
        time.sleep(0.6)
        afterbuy = detail(c)
        print(f"[after-buy] frag={afterbuy.get('frag')} balance={afterbuy.get('balance')} limit={afterbuy.get('limit')}")
        if a.do_upgrade:
            c.evaluate(f"({_CLEAR})(0)"); time.sleep(0.3); c.evaluate(f"({_DRAIN})(0)")
            print(f"[upgrade] {c.evaluate('(' + _CLICK + ')(' + json.dumps(DETAIL + '/btnUnlock') + ')')}")
            time.sleep(2.2)
            upf = c.evaluate(f"({_DRAIN})(0)") or []
            print(f"=== WS after 升級 (skin_up) ({len(upf)}) ===")
            for e in upf: print("  " + fmt(e))
            time.sleep(0.8)
            print(f"[after-upgrade] {json.dumps(detail(c), ensure_ascii=False)}")
        try:
            c._send("Page.enable"); res = c._send("Page.captureScreenshot", {"format": "png"})
            with open(a.shot, "wb") as f: f.write(base64.b64decode(res["data"]))
            print(f"[shot] {a.shot}")
        except Exception as e: print(f"[shot] fail {e}")
        c.close(); return

    if a.cmd == "buy-upgrade":
        # ensure ParkingDecorateView (upgrade panel) is open; reopen via btnSkin if not
        view_open = r"""(vn)=>{const s=cc.director.getScene();const st=[s];
          while(st.length){const n=st.pop();if(n&&n.name===vn&&n.active)return true;
            if(n&&n.children)for(const k of n.children)st.push(k);}return false;}"""
        if not c.evaluate(f"({view_open})({json.dumps('ParkingDecorateView')})"):
            print("[nav] decorate panel closed -> click btnSkin")
            c.evaluate(f"({_CLICK})({json.dumps('/UIRoot/NormalView/ParkingMainView/bottom/btnSkin')})")
            time.sleep(2.2)
        close_detail(c)
        c.evaluate(f"({_CLICK})({json.dumps(f'{TABS}/{a.category}')})"); time.sleep(1.4)
        c.evaluate(f"({_CLICK})({json.dumps(f'{GRID}/' + str(a.cell))})"); time.sleep(1.4)
        before = detail(c)
        print(f"[before] {json.dumps(before, ensure_ascii=False)}")
        if not before.get("open"):
            print("ERR: detail not open"); c.close(); return
        buy_js = f"({_CLICK})({json.dumps(DETAIL + '/btnBuy')})"
        up_js = f"({_CLICK})({json.dumps(DETAIL + '/btnUnlock')})"
        for bi in range(a.buy_times):
            c.evaluate(f"({_CLEAR})(0)"); time.sleep(0.3); c.evaluate(f"({_DRAIN})(0)")
            print(f"[buy {bi+1}/{a.buy_times}] {c.evaluate(buy_js)}")
            time.sleep(2.0)
            buyf = c.evaluate(f"({_DRAIN})(0)") or []
            print(f"=== WS after 購買#{bi+1} ({len(buyf)}) ===")
            for e in buyf: print("  " + fmt(e))
            d = detail(c)
            print(f"  -> frag now {d.get('frag')}  balance {d.get('balance')}  limit {d.get('limit')}")
        if a.do_upgrade:
            time.sleep(0.4); c.evaluate(f"({_DRAIN})(0)")
            print(f"[upgrade] {c.evaluate(up_js)}")
            time.sleep(2.0)
            upf = c.evaluate(f"({_DRAIN})(0)") or []
            print(f"=== WS after 升級 ({len(upf)}) ===")
            for e in upf: print("  " + fmt(e))
        time.sleep(0.8)
        print(f"[after] {json.dumps(detail(c), ensure_ascii=False)}")
        try:
            c._send("Page.enable")
            res = c._send("Page.captureScreenshot", {"format": "png"})
            with open(a.shot, "wb") as f: f.write(base64.b64decode(res["data"]))
            print(f"[shot] {a.shot}")
        except Exception as e: print(f"[shot] fail {e}")
        c.close(); return


if __name__ == "__main__":
    main()
