"""Injected-JS payloads for the dashboard 車位工具 tab.

These run inside the device's live H5 page (via control_panel_app._cdp_json_response
/ _cdp_evaluate). They use only the game's own scene tree + configParking_design,
driving the SAME cocos buy/upgrade UI a human uses (verified 2026-06-15, see
docs/protocol/CARPARK_DECORATION_SHOP.md §9). No new WS protocol is needed.

Two payloads:
  READ_STATE_JS   — navigate to the decorate panel, read 菇車幣 + every OWNED
                    decoration (id, name, level, price, 限購-remaining, and the
                    config-derived remaining-star ladder). Returns JSON.
  EXEC_STEP_JS    — buy N fragments of one decoration via the Mall dialog, then
                    upgrade it one star; returns the WS-confirmed new level.

Robustness: the cocos detail view updates asynchronously after a cell click, so we
POLL until it is stable, and derive the level from the bonus-% text
(per-stat% = 160·(level+1)  ->  level = pct/160 − 1) rather than the animated
star pips. Both payloads are self-contained async arrow functions returning a JSON
string (the dashboard CDP path uses returnByValue + awaitPromise).
"""

# Shared JS helpers, prepended to each payload.
_HELPERS = r"""
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const S = () => cc.director.getScene();
  const findPath = (parts) => { let n = S();
    for (const p of parts) { if (!n || !n.children) return null;
      n = n.children.find(c => (c.name||'') === p); if (!n) return null; } return n; };
  const findByName = (root, nm) => { const st=[root];
    while (st.length) { const n=st.pop(); if (n && n.name===nm) return n;
      if (n && n.children) for (const c of n.children) st.push(c); } return null; };
  const clickNode = (node) => { if (!node) return false;
    try { if (node.emit) node.emit('click', node); } catch(e){}
    try { const b = node.getComponent && node.getComponent('cc.Button');
      if (b && b.clickEvents) for (const ev of b.clickEvents) {
        const t=ev.target, cn=ev._componentName||ev.component, h=ev.handler;
        const comp=t&&t.getComponent&&t.getComponent(cn);
        if (comp && typeof comp[h]==='function') comp[h](null, ev.customEventData); } } catch(e){}
    return true; };
  const clickPath = (parts) => clickNode(findPath(parts));
  const clickIn = (viewName, nodeName) => { const v=findByName(S(),viewName);
    if(!v) return false; return clickNode(findByName(v, nodeName)); };
  const viewOpen = (nm) => { const n=findByName(S(), nm); return !!(n && n.active); };
  const label = (node) => { if(!node) return null;
    for (const c of (node._components||[])) if (c && typeof c.string==='string') return c.string; return null; };
  const labelIn = (viewName, nodeName) => { const v=findByName(S(),viewName);
    if(!v) return null; return label(findByName(v, nodeName)); };
  const parseFirstInt = (s) => { if(!s) return 0; const m=(''+s).match(/(\d+)/); return m?parseInt(m[1],10):0; };
  const HOME_TAB = ['UIRoot','NormalView','MainView','tab','scrollTab','view','content','4'];
  const CARPARK = ['UIRoot','NormalView','MainView','container','MysteryMainView','MysteryMainView','items','CarPark'];
  const BTNSKIN = ['UIRoot','NormalView','ParkingMainView','bottom','btnSkin'];
  const DV='ParkingDecorateView', DETAIL='ParkingDecorateDetailView', MALL='MallTipsView';
  const ensurePanel = async () => {
    if (viewOpen(DV)) return true;
    if (!viewOpen('ParkingMainView')) {
      clickPath(HOME_TAB); await sleep(1600);
      clickPath(CARPARK); await sleep(2500);
    }
    if (viewOpen('ParkingMainView') && !viewOpen(DV)) { clickPath(BTNSKIN); await sleep(2200); }
    return viewOpen(DV);
  };
  // close the detail popup and WAIT until it is actually inactive (so the next
  // cell opens a fresh detail rather than us reading a stale leftover).
  const closeDetail = async () => { const d=findByName(S(),DETAIL);
    if (d && d.active) clickNode(findByName(d,'btnClose'));
    for (let k=0; k<8; k++) { if (!viewOpen(DETAIL)) return; await sleep(150); } };
  const detailField = (parts) => { const r=findByName(S(),DETAIL); if(!r) return null;
    let n=r; for(const p of ['root',...parts]){ if(!n||!n.children) return null;
      n=n.children.find(c=>(c.name||'')===p); if(!n) return null; } return label(n); };
  // level from star pips in nodeShow (5 stars × {one,two,three}); these render in
  // sync with the name/icon, unlike the bonus-% panel which lags.
  const PIP = {one:1, two:2, three:3};
  const detailLevel = () => { const r=findByName(S(),DETAIL); if(!r||!r.active) return 0;
    let n=r; for(const p of ['root','nodeShow','ScrollView','view','content']){
      if(!n||!n.children) return 0; n=n.children.find(c=>(c.name||'')===p); if(!n) return 0; }
    let lvl=0; for(const slot of (n.children||[])) for(const x of (slot.children||[]))
      if(x.active && PIP[x.name]!=null) lvl+=PIP[x.name]; return lvl; };
  // poll until the detail is open and (name, level) are stable across 2 reads
  const readStable = async () => { let prev=null;
    for (let k=0; k<22; k++) {
      const cur = { name: detailField(['nodeShow','txtName']), lvl: detailLevel() };
      if (cur.name && prev && prev.name===cur.name && prev.lvl===cur.lvl) return cur;
      prev = cur; await sleep(170);
    }
    return prev || {name:null, lvl:0}; };
"""

# Config ladder helpers (configParking_design is an in-page global). READ only.
_CONFIG_HELPERS = r"""
  const cfg = (typeof configParking_design !== 'undefined') ? configParking_design : null;
  const rowOf = (id, lv) => { if(!cfg) return null;
    try { return cfg.getDataByKeys('id', id, 'level', lv); } catch(e){ return null; } };
  const attrSum = (oa) => { let s=0; for(const x of (oa||[])) s += (x[1]||0); return s; };
  const fragOf = (id, fromLv) => { const r=rowOf(id, fromLv); if(!r) return 0;
    const e = r.expend && r.expend[0]; return e ? (e[1]||0) : 0; };
  const maxLevel = (id) => { let m=0; for(let lv=1; lv<=20; lv++){ if(rowOf(id,lv)) m=lv; else break; } return m; };
  const buildSteps = (id, curLv) => { const out=[]; const mx=maxLevel(id);
    for(let m=curLv+1; m<=mx; m++){ const frags=fragOf(id, m-1);
      const a0=attrSum((rowOf(id,m-1)||{}).own_attrs), a1=attrSum((rowOf(id,m)||{}).own_attrs);
      out.push([m, frags, a1-a0]); } return out; };
  const nameToId = (() => { const map={}; if(!cfg) return map;
    try { const ds = cfg.getDatas ? cfg.getDatas() : []; const arr = Array.isArray(ds)?ds:Object.values(ds||{});
      for (const r of arr) { if (r && r.name && map[r.name]===undefined) map[r.name]=r.id; } } catch(e){}
    return map; })();
"""

READ_STATE_JS = r"""
async () => {
""" + _HELPERS + _CONFIG_HELPERS + r"""
  if (!cfg) return JSON.stringify({error:'no_config'});
  if (!(await ensurePanel())) return JSON.stringify({error:'cannot_open_decorate_panel'});
  const GRID = '/UIRoot/NormalView/ParkingDecorateView/root/ScrollView/view/content';
  const TABS = '/UIRoot/NormalView/ParkingDecorateView/root/ScrollView-001/view/content';
  const gridContent = () => findPath(GRID.split('/').filter(Boolean));
  let coin = null; const decos = []; const seen = {};
  for (let ci=0; ci<5; ci++) {
    await closeDetail();
    clickPath((TABS + '/' + ci).split('/').filter(Boolean)); await sleep(1300);
    const gc = gridContent(); if (!gc) continue;
    const n = gc.children.length;
    for (let i=0; i<n; i++) {
      clickPath((GRID + '/' + i).split('/').filter(Boolean));
      const st = await readStable();
      const name = st.name, lvl = st.lvl;
      if (coin === null) { const cv = parseFirstInt(detailField(['nodeItem','Label'])); if (cv>0) coin = cv; }
      if (!name) { await closeDetail(); continue; }
      const id = nameToId[name];
      if (id===undefined || seen[id]) { await closeDetail(); continue; }
      seen[id] = true;
      const steps = (lvl>=1) ? buildSteps(id, lvl) : [];
      const price = parseFirstInt(detailField(['btnBuy','num']));
      const limit = parseFirstInt(detailField(['item','txtLimit']));  // "限購：108/120" -> 108 (剩餘)
      decos.push({ id, name, level: lvl, price, limit_remaining: limit, steps, cat: ci, cell: i });
      await closeDetail();
    }
  }
  return JSON.stringify({ coin: coin||0, decos });
}
"""

# Buy `qty` fragments of the decoration in `category`/`cell` via the Mall dialog,
# then (if doUpgrade) press 升級. Returns the new level + step outcomes.
EXEC_STEP_JS = r"""
async (args) => {
""" + _HELPERS + r"""
  const [category, cell, qty, doUpgrade, expectName] = args;
  const GRID = '/UIRoot/NormalView/ParkingDecorateView/root/ScrollView/view/content';
  const TABS = '/UIRoot/NormalView/ParkingDecorateView/root/ScrollView-001/view/content';
  if (!(await ensurePanel())) return JSON.stringify({ok:false, err:'cannot_open_panel'});
  await closeDetail();
  clickPath((TABS + '/' + category).split('/').filter(Boolean)); await sleep(1200);
  clickPath((GRID + '/' + cell).split('/').filter(Boolean));
  const st = await readStable();
  const beforeName = st.name, beforeLvl = st.lvl;
  if (expectName && beforeName !== expectName)
    return JSON.stringify({ok:false, err:'wrong_cell', want:expectName, got:beforeName});
  const d = findByName(S(), DETAIL); if(!d || !d.active) return JSON.stringify({ok:false, err:'no_detail'});
  clickNode(findByName(d, 'btnBuy')); await sleep(1700);
  if (!viewOpen(MALL)) return JSON.stringify({ok:false, err:'no_mall_dialog', name:beforeName});
  // dialog opens at qty 1; raise to `qty` using +5 (btnAddTen) then +1 (btnAdd)
  let need = Math.max(0, qty - 1);
  while (need >= 5) { clickIn(MALL, 'btnAddTen'); await sleep(220); need -= 5; }
  while (need > 0) { clickIn(MALL, 'btnAdd'); await sleep(200); need -= 1; }
  const setQty = parseInt(labelIn(MALL, 'EditBox')||'0', 10);
  if (setQty !== qty) { clickIn(MALL, 'btnClose'); return JSON.stringify({ok:false, bought:false, err:'qty_mismatch', want:qty, got:setQty, name:beforeName}); }
  clickIn(MALL, 'btnBuy'); await sleep(2200);
  // frag count owned AFTER the buy, BEFORE the upgrade ("X/Y" -> X). The buy is
  // atomic (qty all-or-nothing), so X>=qty means the purchase committed (coin spent).
  const fragAfter = detailField(['item','txtNext']);
  const bought = parseFirstInt(fragAfter) >= qty;
  if (!bought)
    return JSON.stringify({ok:false, bought:false, err:'buy_failed', name:beforeName, frag_after:fragAfter});
  let afterLvl = beforeLvl;
  if (doUpgrade) {
    const dd = findByName(S(), DETAIL);
    if (!dd) return JSON.stringify({ok:false, bought:true, err:'no_detail_for_upgrade', name:beforeName, before_level:beforeLvl, frag_after:fragAfter});
    clickNode(findByName(dd, 'btnUnlock'));
    const st2 = await readStable();
    afterLvl = st2.lvl;
    // upgrade must actually raise the star, else the buy was wasted — report failure
    if (afterLvl <= beforeLvl)
      return JSON.stringify({ok:false, bought:true, err:'upgrade_no_levelup', name:beforeName, before_level:beforeLvl, after_level:afterLvl, frag_after:fragAfter});
  }
  return JSON.stringify({ ok:true, bought:true, name:beforeName, before_level:beforeLvl,
    frag_after:fragAfter, after_level:afterLvl });
}
"""
