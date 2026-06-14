"""Read-only inventory scan of all 5 decoration categories on a live device.

For each decoration cell: name, current star level (sum of pips), current/next
bonus %, fragments owned/required, 限購 (purchase cap), buy cost, skin effect.
Clicking a cell only opens its detail popup (no spend). Output -> JSON.
"""
from __future__ import annotations
import argparse, io, json, sys, time
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
GAME_HOST = "mushroomh5.acenetgame.com"

_CLICK_JS = r"""
([path]) => {
  const scene=cc.director.getScene();
  const find=(r,parts)=>{let n=r;for(const p of parts){if(!n||!n.children)return null;
    n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
  const node=find(scene, path.split('/').filter(Boolean));
  if(!node) return {ok:false, err:'not_found'};
  try{ node.emit('click', node); return {ok:true}; }catch(e){return {ok:false,err:String(e)};}
}
"""

_CELL_COUNT_JS = r"""
([gridPath]) => {
  const scene=cc.director.getScene();
  const find=(r,parts)=>{let n=r;for(const p of parts){if(!n||!n.children)return null;
    n=n.children.find(c=>(c.name||'')===p);if(!n)return null;}return n;};
  const content=find(scene, gridPath.split('/').filter(Boolean));
  return content ? content.children.length : 0;
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
  const labStr=(n)=>{ if(!n)return null; for(const c of (n._components||[]))
    if(c && typeof c.string==='string') return c.string; return null; };
  const out={};
  out.name = labStr(find(root,['root','nodeShow','txtName']));
  out.cur = labStr(find(root,['root','nodeChange','ScrollView','view','content','0','txtBase']));
  out.next = labStr(find(root,['root','nodeChange','ScrollView','view','content','0','txtNext']));
  out.frag = labStr(find(root,['root','item','txtNext']));
  out.limit = labStr(find(root,['root','item','txtLimit']));
  out.buy_cost = labStr(find(root,['root','btnBuy','num']));
  out.skill = labStr(find(root,['root','nodeSkill','txtDesc']));
  out.top_label = labStr(find(root,['root','nodeTop','txtLabel']));
  // current star level = sum of pips across 5 slots in nodeShow stars
  const starContent = find(root,['root','nodeShow','ScrollView','view','content']);
  let lvl=0; const PIP={one:1,two:2,three:3};
  if(starContent) for(const slot of (starContent.children||[])){
    for(const f of (slot.children||[])) if(f.active && PIP[f.name]!=null) lvl+=PIP[f.name];
  }
  out.level = lvl;
  // button states
  const bU=find(root,['root','btnUnlock']); out.btnUnlock_active = bU?bU.active:null;
  const bB=find(root,['root','btnBuy']); out.btnBuy_active = bB?bB.active:null;
  return out;
}
"""

CATS = ["大門","場景","圍欄","路燈","裝飾"]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--port",type=int,default=9223)
    ap.add_argument("--out", default="logs/_scratch/decor_inventory.json")
    ap.add_argument("--max-cells", type=int, default=20)
    a=ap.parse_args()
    from playwright.sync_api import sync_playwright
    pw=sync_playwright().start(); b=pw.chromium.connect_over_cdp(f"http://127.0.0.1:{a.port}")
    page=None
    for ctx in b.contexts:
        for p in ctx.pages:
            if GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url: page=p; break
        if page: break
    if not page: pw.stop(); raise SystemExit("no page")

    grid="/UIRoot/NormalView/ParkingDecorateView/root/ScrollView/view/content"
    tab_base="/UIRoot/NormalView/ParkingDecorateView/root/ScrollView-001/view/content"
    inventory={}
    for ci, cat in enumerate(CATS):
        page.evaluate(_CLICK_JS, [f"{tab_base}/{ci}"])
        time.sleep(1.6)
        n=page.evaluate(_CELL_COUNT_JS, [grid])
        n=min(n, a.max_cells)
        rows=[]
        for i in range(n):
            r=page.evaluate(_CLICK_JS, [f"{grid}/{i}"])
            if not r.get("ok"): continue
            time.sleep(0.9)
            d=page.evaluate(_READ_DETAIL_JS, [])
            d["cell"]=i
            rows.append(d)
            print(f"[{cat}] cell{i}: {d.get('name')!r} lv={d.get('level')} "
                  f"cur={d.get('cur')} next={d.get('next')} frag={d.get('frag')} "
                  f"limit={d.get('limit')} buy={d.get('buy_cost')}")
        inventory[cat]=rows
    with open(a.out,"w",encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {a.out}")
    pw.stop()

if __name__=="__main__":
    main()
