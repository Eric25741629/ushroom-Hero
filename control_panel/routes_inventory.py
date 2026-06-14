"""倉庫 dashboard blueprint — 守護靈 (Task 4) + 神器附魔石 (Task 3) 唯讀檢視 + 過濾.

資料來源走純 WS（不踢線）：route 用 ``_cpa._cdp_json_response(ip, JS, await_promise=True)``
在裝置 live CDP 上注入 JS，JS 經遊戲自身 ``netManager._cnet`` 送查詢 cmd、hook
``reciveMsg`` 收同 cmd 的「已解密」回包，再用內嵌的 minimal protobuf walker 解出欄位，
並用 ``configAttribute._data[2]`` / ``configSpirit._data[6]`` 對名稱。

協議（5554 live 驗證 2026-06-14）：
  - 守護靈 spirit.spirit_info  cmd 19713 (0x4D01) empty body
      s2c {reset_times#1, reshape_times#2, tab#3, tab_list#4, spirit_list#5:p_spirit_card[]}
      p_spirit_card {id#1, config_id#2, level#3, is_lock#4, pos_list#5:p_spirit_pos[]}
      p_spirit_pos {pos#1, cur_id#2, cur_attr_list#3:p_key_value[], reshape_id#4, reshape_attr_list#5}
  - 神器附魔 artifact_gem.artifact_gem_info  cmd 13569 (0x3501) empty body
      s2c { p_artifact_gem[] at f2 }
      p_artifact_gem {id#1, quality#2, pos#3, suit#4, lv#5, exp#6, is_red#7, is_lock#8,
                      base_attr#9:p_key_value[], rand_attr#10:p_key_value[]}

分解/賣出/升級（mutate）cmd 已由「宣告序=index」推導（info=idx1=0x3501 已驗證）：
  split(分解)=0x350A, sub=0x350B, up(升級)=0x3503, lock=0x3506。**body schema 待一次 live 觸發
  擷取**，故動作 route 目前回 501 pending（避免盲射 destructive cmd）。「聯合搜索」= 前端依
  屬性/套裝/品質過濾（client-side），不需新 WS。
"""
import json  # noqa: F401  (kept for parity with sibling blueprints / future use)

from flask import Blueprint, jsonify, render_template

from control_panel.shared.auth import _fly_pet_auth

bp = Blueprint("inventory", __name__)


# --- 神器附魔 mutate cmd ids（推導；body 待 live 擷取）------------------------
ARTIFACT_CMD_INFO = 0x3501     # artifact_gem_info（已驗證 read）
ARTIFACT_CMD_UP = 0x3503       # artifact_gem_up（升級）
ARTIFACT_CMD_LOCK = 0x3506     # artifact_gem_lock
ARTIFACT_CMD_SPLIT = 0x350A    # artifact_gem_split（分解）
ARTIFACT_CMD_SUB = 0x350B      # artifact_gem_sub（批量分解/賣）


# --- 共用 minimal protobuf walker（注入遊戲頁，解 reciveMsg 交回的 Uint8Array）----
_PB_HELPERS_JS = r"""
  function readVarint(buf, off){ let v=0n, s=0n;
    while(true){ const b=buf[off++]; v |= BigInt(b & 0x7f) << s; if(!(b & 0x80)) break; s += 7n; }
    return [v, off]; }
  function walk(buf){ const out=[]; let off=0;
    while(off < buf.length){
      let r=readVarint(buf, off); off=r[1]; const t=Number(r[0]); const f=t>>3, w=t&7;
      if(w===0){ let r2=readVarint(buf, off); off=r2[1]; out.push({f:f,w:0,v:r2[0]}); }
      else if(w===2){ let r2=readVarint(buf, off); off=r2[1]; const L=Number(r2[0]);
        out.push({f:f,w:2,v:buf.slice(off, off+L)}); off+=L; }
      else if(w===1){ out.push({f:f,w:1,v:0n}); off+=8; }
      else if(w===5){ out.push({f:f,w:5,v:0n}); off+=4; }
      else { break; }
    } return out; }
  function num(v){ return (typeof v==='bigint') ? Number(v) : v; }
  function kvs(buf, field){ const m=[];
    for(const e of walk(buf)){ if(e.f===field && e.w===2){ const d=walk(e.v);
      let k=null, val=null; for(const x of d){ if(x.f===1) k=num(x.v); if(x.f===2) val=num(x.v); }
      if(k!==null) m.push({attr_id:k, value:val}); } } return m; }
  function attrName(id){ try{ const o=configAttribute.getDataByKey(id);
    if(o && o._data) return String(o._data[2]||id); }catch(e){} return String(id); }
  function attrs(buf, field){ return kvs(buf, field).map(function(a){
    return {attr_id:a.attr_id, name:attrName(a.attr_id), value:a.value}; }); }
"""


_SPIRIT_JS = "new Promise((resolve) => {" + _PB_HELPERS_JS + r"""
  function done(o){ try { resolve(JSON.stringify(o)); } catch(e){ resolve(JSON.stringify({error:'stringify'})); } }
  function spiritName(cfg){ try{ const o=configSpirit.getDataByKey(cfg);
    if(o && o._data){ const p=String(o._data[6]||''); const b=p.split('/').pop(); return b||String(cfg);} }catch(e){}
    return String(cfg); }
  function pos(buf){ const d=walk(buf); let p=0,cid=0,rid=0;
    for(const x of d){ if(x.f===1&&x.w===0)p=num(x.v); if(x.f===2&&x.w===0)cid=num(x.v); if(x.f===4&&x.w===0)rid=num(x.v); }
    return {pos:p, cur_id:cid, reshape_id:rid, affixes:attrs(buf,3), reshape:attrs(buf,5)}; }
  function card(buf){ const d=walk(buf); let id=0n,cfg=0,lv=0,lk=0;
    for(const x of d){ if(x.f===1)id=x.v; else if(x.f===2&&x.w===0)cfg=num(x.v);
      else if(x.f===3&&x.w===0)lv=num(x.v); else if(x.f===4&&x.w===0)lk=num(x.v); }
    const ps=[]; for(const x of d){ if(x.f===5&&x.w===2) ps.push(pos(x.v)); }
    return {id:String(id), config_id:cfg, name:spiritName(cfg), level:lv, lock: lk?1:0, positions:ps}; }
  try {
    const sock = netManager._cnet;
    const orig = sock.reciveMsg.bind(sock); let cleared=false;
    const cleanup=function(){ if(!cleared){cleared=true; sock.reciveMsg=orig;} };
    const tid=setTimeout(function(){ cleanup(); done({error:'timeout waiting spirit_info'}); }, 8000);
    sock.reciveMsg=function(c,b){ if(c===19713 && !cleared){ clearTimeout(tid); cleanup();
        try{ const top=walk(b); const sp=[]; let rs=0,rsh=0,tab=0;
          for(const x of top){ if(x.f===1&&x.w===0)rs=num(x.v); else if(x.f===2&&x.w===0)rsh=num(x.v);
            else if(x.f===3&&x.w===0)tab=num(x.v); else if(x.f===5&&x.w===2) sp.push(card(x.v)); }
          sp.sort(function(a,b){ return b.level-a.level || a.config_id-b.config_id; });
          done({reset_times:rs, reshape_times:rsh, tab:tab, count:sp.length, spirits:sp});
        }catch(e){ done({error:String(e)+(e.stack||'')}); } }
      return orig(c,b); };
    sock.sendMessage(19713, new Uint8Array());
  } catch(e){ done({error:String(e)}); }
})"""


_GEM_JS = "new Promise((resolve) => {" + _PB_HELPERS_JS + r"""
  function done(o){ try { resolve(JSON.stringify(o)); } catch(e){ resolve(JSON.stringify({error:'stringify'})); } }
  function gem(buf){ const d=walk(buf);
    const g={id:'0',quality:0,pos:0,suit:0,lv:0,exp:0,is_red:0,is_lock:0};
    for(const x of d){
      if(x.f===1)g.id=String(x.v);
      else if(x.f===2&&x.w===0)g.quality=num(x.v);
      else if(x.f===3&&x.w===0)g.pos=num(x.v);
      else if(x.f===4&&x.w===0)g.suit=num(x.v);
      else if(x.f===5&&x.w===0)g.lv=num(x.v);
      else if(x.f===6&&x.w===0)g.exp=num(x.v);
      else if(x.f===7&&x.w===0)g.is_red=num(x.v);
      else if(x.f===8&&x.w===0)g.is_lock=num(x.v);
    }
    g.base_attr=attrs(buf,9); g.rand_attr=attrs(buf,10); return g; }
  try {
    const sock = netManager._cnet;
    const orig = sock.reciveMsg.bind(sock); let cleared=false;
    const cleanup=function(){ if(!cleared){cleared=true; sock.reciveMsg=orig;} };
    const tid=setTimeout(function(){ cleanup(); done({error:'timeout waiting artifact_gem_info'}); }, 8000);
    sock.reciveMsg=function(c,b){ if(c===13569 && !cleared){ clearTimeout(tid); cleanup();
        try{ const top=walk(b); const gems=[];
          for(const x of top){ if(x.f===2&&x.w===2) gems.push(gem(x.v)); }
          gems.sort(function(a,b){ return a.quality-b.quality || a.lv-b.lv; });
          done({count:gems.length, gems:gems});
        }catch(e){ done({error:String(e)+(e.stack||'')}); } }
      return orig(c,b); };
    sock.sendMessage(13569, new Uint8Array());
  } catch(e){ done({error:String(e)}); }
})"""


@bp.route("/inventory")
@_fly_pet_auth
def inventory_page():
    """守護靈 + 神器附魔石 倉庫檢視頁（過濾 / 賣最低排序 / 分解勾選）。"""
    return render_template("inventory.html")


@bp.route("/api/spirit_list/<ip>", methods=["GET"])
@_fly_pet_auth
def spirit_list(ip):
    """守護靈倉庫：每隻守護靈 + 各位置詞條（cur_attr）。純 WS 唯讀 (cmd 19713)。"""
    import control_panel_app as _cpa

    return _cpa._cdp_json_response(ip, _SPIRIT_JS, await_promise=True, data_key="data")


@bp.route("/api/artifact_gem_list/<ip>", methods=["GET"])
@_fly_pet_auth
def artifact_gem_list(ip):
    """神器附魔石倉庫：每顆寶石品質/等級/主屬/隨機詞條。純 WS 唯讀 (cmd 0x3501)。"""
    import control_panel_app as _cpa

    return _cpa._cdp_json_response(ip, _GEM_JS, await_promise=True, data_key="data")


@bp.route("/api/artifact_gem_action/<ip>", methods=["POST"])
@_fly_pet_auth
def artifact_gem_action(ip):
    """分解 / 賣出 動作 — cmd 已推導，body schema 待一次 live 擷取後啟用。

    回 501 pending（不盲射 destructive cmd）。前端先具備勾選/排序 UI，待 body 補上即可接線。
    """
    return jsonify({
        "status": "pending",
        "message": ("分解/賣出 body schema 待 live 擷取一次再啟用；"
                    "cmd 已推導 split(分解)=0x350A sub=0x350B up(升級)=0x3503"),
        "cmds": {"split": ARTIFACT_CMD_SPLIT, "sub": ARTIFACT_CMD_SUB,
                 "up": ARTIFACT_CMD_UP, "lock": ARTIFACT_CMD_LOCK},
    }), 501
