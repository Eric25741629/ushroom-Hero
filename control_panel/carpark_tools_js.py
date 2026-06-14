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



READ_STATE_WS_JS = r"""
() => new Promise((resolve) => {
  // --- minimal protobuf walker (proto3 wire) ---
  function rv(b, o){ let v=0n, s=0n; while(true){ const c=b[o++]; v|=BigInt(c&0x7f)<<s; if(!(c&0x80)) break; s+=7n; } return [v,o]; }
  function walk(b){ const out=[]; let o=0; while(o<b.length){ let r=rv(b,o); o=r[1]; const t=Number(r[0]), f=t>>3, wt=t&7;
    if(wt===0){ let r2=rv(b,o); o=r2[1]; out.push({f, w:0, v:r2[0]}); }
    else if(wt===2){ let r2=rv(b,o); o=r2[1]; const L=Number(r2[0]); out.push({f, w:2, v:b.slice(o,o+L)}); o+=L; }
    else if(wt===1){ out.push({f, w:1}); o+=8; }
    else if(wt===5){ out.push({f, w:5}); o+=4; }
    else break; } return out; }
  const N = v => typeof v==='bigint' ? Number(v) : v;

  // --- config joins (client tables, already loaded in-page) ---
  // frag_goods_id -> {shop_id, price, cap} from configMall shop_type 11.
  const fragShop = {};
  for (const row of Object.values(configMall.getDatas() || {})) { const d = row._data || row;
    if (Array.isArray(d) && d[1] === 11 && Array.isArray(d[2]))
      fragShop[d[2][0]] = { shop_id: d[0], price_cur: d[3] && d[3][0], price: d[3] && d[3][1], cap: d[8] }; }
  const pdRow = (id, lv) => { try { return configParking_design.getDataByKeys('id', id, 'level', lv); } catch(e){ return null; } };
  const fragGoodsOf = (id) => { for (let lv=1; lv<=15; lv++){ const r=pdRow(id,lv); if(r && r.expend && r.expend[0]) return r.expend[0][0]; } return null; };
  const nameOf = (id) => { const r = pdRow(id,1) || pdRow(id,0); return r ? r.name : String(id); };
  const maxLevelOf = (id) => { let m=0; for(let lv=1; lv<=15; lv++){ if(pdRow(id,lv)) m=lv; else break; } return m; };
  const attrSum = (oa) => { let s=0; for(const x of (oa||[])) s += (x[1]||0); return s; };
  // remaining single-star upgrade ladder: [to_level, frags, marginal_attr]
  const buildSteps = (id, curLv) => { const out=[]; const mx=maxLevelOf(id);
    for(let m=curLv+1; m<=mx; m++){ const rPrev=pdRow(id,m-1), rCur=pdRow(id,m);
      const frags = (rPrev && rPrev.expend && rPrev.expend[0]) ? rPrev.expend[0][1] : 0;
      out.push([m, frags, attrSum(rCur && rCur.own_attrs) - attrSum(rPrev && rPrev.own_attrs)]); }
    return out; };

  // --- briefly wrap IS() to grab the role/goods data singleton (stable) ---
  const origIS = window.IS; let roleModel = null;
  window.IS = function(t){ let inst; try { inst = origIS(t); } catch(e){ return origIS(t); }
    try { if(!roleModel && inst && typeof inst.GetRoleAttr==='function' && typeof inst.GetRoleId==='function') roleModel = inst; } catch(e){}
    return inst; };

  const sock = netManager._cnet; const orig = sock.reciveMsg.bind(sock); const got = {}; let cleared = false;
  const cleanup = () => { if(!cleared){ cleared = true; sock.reciveMsg = orig; window.IS = origIS; } };
  sock.reciveMsg = function(c, b){ c = c|0;
    if((c===12801 || c===6913) && !got[c] && b){ let by;
      if(b instanceof Uint8Array) by=b; else if(b && b.buffer) by=new Uint8Array(b.buffer, b.byteOffset||0, b.byteLength);
      if(by) got[c] = by.slice(); }
    return orig(c, b); };

  // step 1: let the game loop call IS() once so roleModel is captured, derive
  // my role id, then fire both reads.
  setTimeout(() => {
    let role = (roleModel && roleModel.GetRoleId) ? Number(roleModel.GetRoleId()) : 0;
    if(!role){ const cnt={}; for(const k of Object.keys(localStorage)){ const m=k.match(/(\d{12,})$/); if(m) cnt[m[1]]=(cnt[m[1]]||0)+1; }
      const top=Object.entries(cnt).sort((a,b)=>b[1]-a[1])[0]; role = top ? Number(top[0]) : 0; }
    netManager.send("shop.shop_info_c2s", { shop_type: 11 }, true);
    netManager.send("car_park.car_park_info_c2s", { type: 0, master_id: role, ceng: 0 }, true);

    // step 2: wait for both s2c, then decode + join.
    setTimeout(() => {
      cleanup();
      const res = { role_id: String(role) };
      try { res.coin = roleModel ? roleModel.GetRoleAttr(201) : null; } catch(e){ res.coin = null; }
      const buy = {};
      if(got[6913]) for(const x of walk(got[6913])) if(x.f===2 && x.w===2){ const d=walk(x.v); let k=null, vv=null;
        for(const y of d){ if(y.f===1) k=N(y.v); if(y.f===2) vv=N(y.v); } if(k!=null) buy[k]=vv; }
      const skins = [];
      if(got[12801]) for(const x of walk(got[12801])) if(x.f===8 && x.w===2){ const d=walk(x.v); let id=0, lev=0;
        for(const y of d){ if(y.f===1) id=N(y.v); if(y.f===2) lev=N(y.v); } skins.push([id, lev]); }
      const decos = [];
      for(const [id, lev] of skins){ if(lev < 1) continue;   // lev 0 == 免費初始款
        const fg = fragGoodsOf(id); const sh = (fg!=null) ? fragShop[fg] : null;
        const bought = sh ? (buy[sh.shop_id] || 0) : null;
        decos.push({ id, name: nameOf(id), level: lev, frag_goods: (fg!=null?fg:null),
          shop_id: (sh ? sh.shop_id : null), price: (sh ? sh.price : null), cap: (sh ? sh.cap : null),
          bought: bought, limit_remaining: (sh && sh.cap!=null) ? (sh.cap - bought) : null,
          steps: buildSteps(id, lev) }); }
      decos.sort((a,b) => a.id - b.id);
      res.deco_count = decos.length; res.decos = decos;
      res.ok = !!(got[12801]);
      resolve(JSON.stringify(res));
    }, 1500);
  }, 350);
})
"""


EXEC_STEP_WS_JS = r"""
(args) => new Promise((resolve)=>{
  const [SHOP_ID, SKIN_ID, FRAGS, DO_UPGRADE] = args;
  function rv(b,o){let v=0n,s=0n;while(true){const c=b[o++];v|=BigInt(c&0x7f)<<s;if(!(c&0x80))break;s+=7n;}return [v,o];}
  function walk(b){const out=[];let o=0;while(o<b.length){let r=rv(b,o);o=r[1];const t=Number(r[0]),f=t>>3,wt=t&7;
    if(wt===0){let r2=rv(b,o);o=r2[1];out.push({f,w:0,v:r2[0]});}
    else if(wt===2){let r2=rv(b,o);o=r2[1];const L=Number(r2[0]);out.push({f,w:2,v:b.slice(o,o+L)});o+=L;}
    else if(wt===1){out.push({f,w:1});o+=8;}else if(wt===5){out.push({f,w:5});o+=4;}else break;}return out;}
  const N=v=>typeof v==='bigint'?Number(v):v;
  const nameOf=(id)=>{try{const r=configParking_design.getDataByKeys('id',id,'level',1);return r?r.name:String(id);}catch(e){return String(id);}};

  const sock=netManager._cnet; const orig=sock.reciveMsg.bind(sock);
  const waiters=[]; let installed=true;
  sock.reciveMsg=function(c,b){ c=c|0;
    for(let i=waiters.length-1;i>=0;i--){ const w=waiters[i]; if(w.cmds.has(c)){ let by;
      if(b instanceof Uint8Array)by=b; else if(b&&b.buffer)by=new Uint8Array(b.buffer,b.byteOffset||0,b.byteLength);
      clearTimeout(w.timer); waiters.splice(i,1); w.resolve({cmd:c, body: by?by.slice():null}); } }
    return orig(c,b); };
  function waitFor(cmds, ms){ return new Promise((res)=>{ const w={cmds:new Set(cmds), resolve:res};
    w.timer=setTimeout(()=>{ const i=waiters.indexOf(w); if(i>=0)waiters.splice(i,1); res({cmd:0,body:null,timeout:true}); }, ms);
    waiters.push(w); }); }
  function uninstall(){ if(installed){ installed=false; sock.reciveMsg=orig; } }

  const origIS=window.IS; let roleModel=null;
  window.IS=function(t){let inst;try{inst=origIS(t);}catch(e){return origIS(t);}
    try{if(!roleModel&&inst&&typeof inst.GetRoleAttr==='function'&&typeof inst.GetRoleId==='function')roleModel=inst;}catch(e){}return inst;};
  function getRole(){ let r = roleModel?Number(roleModel.GetRoleId()):0;
    if(!r){const cnt={};for(const k of Object.keys(localStorage)){const m=k.match(/(\d{12,})$/);if(m)cnt[m[1]]=(cnt[m[1]]||0)+1;}
      const top=Object.entries(cnt).sort((a,b)=>b[1]-a[1])[0];r=top?Number(top[0]):0;} return r; }

  async function readLevel(role){ const p=waitFor([12801],6000);
    netManager.send("car_park.car_park_info_c2s",{type:0,master_id:role,ceng:0},true);
    const r=await p; if(!r.body) return null;
    for(const x of walk(r.body)) if(x.f===8&&x.w===2){ const d=walk(x.v); let id=0,lev=0;
      for(const y of d){if(y.f===1)id=N(y.v);if(y.f===2)lev=N(y.v);} if(id===SKIN_ID) return lev; }
    return 0; }

  async function run(){
    const name=nameOf(SKIN_ID);
    const role=getRole();
    if(!role){ uninstall(); window.IS=origIS; return {ok:false, bought:false, err:'no_role_id', name}; }
    const before=await readLevel(role);
    let bought=false;
    if(FRAGS>0){ const p=waitFor([6914,513],8000);
      netManager.send("shop.shop_buy_c2s",{shop_type:11,shop_id:SHOP_ID,num:FRAGS},true);
      const r=await p;
      if(r.cmd===6914) bought=true;
      else { uninstall(); window.IS=origIS; return {ok:false, bought:false, name, before_level:before,
        err:(r.timeout?'buy_timeout':'buy_rejected_'+r.cmd)}; }
    } else bought=true;
    if(DO_UPGRADE){ const p=waitFor([12817,513],8000);
      netManager.send("car_park.car_park_skin_up_c2s",{type:0,skin_id:SKIN_ID},true);
      const r=await p;
      if(r.cmd!==12817){ uninstall(); window.IS=origIS; return {ok:false, bought, name, before_level:before, after_level:before,
        err:(r.timeout?'upgrade_timeout':'upgrade_rejected_'+r.cmd)}; }
      const after=await readLevel(role); uninstall(); window.IS=origIS;
      if(after===null||after<=before) return {ok:false, bought, name, before_level:before, after_level:after, err:'upgrade_no_levelup'};
      return {ok:true, bought, name, before_level:before, after_level:after};
    }
    uninstall(); window.IS=origIS;
    return {ok:true, bought, name, before_level:before, after_level:before};
  }
  run().then(r=>resolve(JSON.stringify(r))).catch(e=>{ try{uninstall();window.IS=origIS;}catch(_){} resolve(JSON.stringify({ok:false,err:'exc:'+e})); });
})
"""
