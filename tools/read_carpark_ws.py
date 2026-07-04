"""Pure-WS read of car-park decoration state (no cocos UI navigation).

Replaces the slow ParkingDecorateView scene-tree walk (~90s, see
control_panel/carpark_tools_js.py READ_STATE_JS) with three data sources that
are already in the live page:

  1. skin_list (已擁有裝飾 + 等級)  -> WS car_park.car_park_info_c2s (12801)
       body {type:0, master_id:<my role id>, ceng:0}
       s2c 12801 -> skin_list#8 repeated p_car_park_skin {skin_id#1, skin_lev#2}
  2. 碎片購買數 (限購已買)          -> WS shop.shop_info_c2s (6913)
       body {shop_type:11}
       s2c 6913 -> buy_info#2 repeated p_key_value {k=shop_id#1, v=bought#2}
  3. 菇車幣餘額                    -> role attribute 201 (currency, gtid<1000)
       roleModel.GetRoleAttr(201)  (== roleInfo[201]); roleModel is the IS()
       singleton carrying GetRoleAttr/GetRoleId.

  item_id (shop_id) + 碎片單價 + 限購上限 come from the CLIENT config table
  ``configMall`` (rows where _data[1]===11): _data[0]=shop_id, _data[2]=[frag_goods,qty],
  _data[3]=[currency, price], _data[8]=cap. NOT from the WS packet (shop_info only
  returns the per-shop_id bought counts). 限購剩餘 = cap - bought.

The fragment goods of a decoration = ``configParking_design`` expend[0][0]; the
join is decoration.id -> frag_goods -> configMall shop row.

LIVE-verified 2026-06-15 (5556/role 89562953025122, CDP 9226):
  菇車幣 88,643,235; 中式庭院大門(10003) lv9 -> shop 1705, 30萬/碎片, cap120,
  bought25, remaining95 (= cumulative-frags-at-lv9, matches §9.2).

Read-only / non-destructive (two read cmds + a config join). Safe to run while
the bot idles. See docs/protocol/CARPARK_DECORATION_SHOP.md §10.

    python tools/read_carpark_ws.py --port 9226
"""
from __future__ import annotations

import argparse
import io
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # py3.7+, avoids GC-closing the buffer
except Exception:  # noqa: BLE001
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, "tools")
from rawcdp import RawCDP  # noqa: E402

# One self-contained async payload: grabs the role/goods model by briefly
# wrapping the global IS() singleton-accessor, sends the two read cmds via the
# game's own netManager (which encodes the protobuf), hooks reciveMsg for the
# decoded s2c bodies, then joins against configMall + configParking_design.
READ_CARPARK_WS_JS = r"""
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--json", action="store_true", help="print raw JSON instead of a table")
    args = ap.parse_args()

    c = RawCDP(args.port, timeout=20.0)
    c.enable_runtime()
    raw = c.evaluate(f"({READ_CARPARK_WS_JS})()")
    c.close()
    d = json.loads(raw) if isinstance(raw, str) else raw

    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return

    print(f"role_id={d.get('role_id')}  菇車幣={d.get('coin')}  "
          f"owned_decorations={d.get('deco_count')}  ok={d.get('ok')}")
    print(f"{'id':>6} {'name':<12} {'lv':>3} {'shop':>6} {'price':>8} "
          f"{'cap':>4} {'bought':>6} {'remain':>6}  next_step")
    for x in (d.get("decos") or []):
        steps = x.get("steps") or []
        nxt = (f"+1->{steps[0][0]} ({steps[0][1]}碎片,+{steps[0][2]}屬)"
               if steps else "MAX")
        print(f"{x['id']:>6} {str(x['name'])[:12]:<12} {x['level']:>3} "
              f"{str(x.get('shop_id')):>6} {str(x.get('price')):>8} "
              f"{str(x.get('cap')):>4} {str(x.get('bought')):>6} "
              f"{str(x.get('limit_remaining')):>6}  {nxt}")


if __name__ == "__main__":
    main()
