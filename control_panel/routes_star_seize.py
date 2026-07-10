"""星據車位(奇星車場)搶佔 route(獨立頁面 /star-seize + API)。

跨界車位第一列 4 槽的佇列制佔領戰(server_car_* protocol, car_park module):
- ``server_car_info``  12860 (0x323c) — 讀 4 槽狀態 + 頂層攻/守死亡冷卻。
- ``server_car_queue`` 12868 — 讀某槽守隊首位配置/戰力。
- ``server_car_join``  12861 (0x323d) — 加入搶佔(queue_type=1)/駐守(queue_type=2)。

**已 live 驗證(小寶 9226)必修:冷送無效 → 必須「開 view 再送」。**
`server_car_join` 只有在該槽的 ``ParkingCrossServerView`` 已由完整進入序列打開時
才被接受;從 ParkingMainView 冷送不進佇列。因此 ``/seize`` 改為 Python 端多步
orchestration(數次 ``control_panel_app._cdp_evaluate``):確保在 arena → tap
building 比對標題 pos → 讀 server_car_info 驗證全閘(含 attack_cd 死亡冷卻、
台灣時區 +8h 休戰) → 全過才 emit「搶佔」→「加入搶佔」→「確定」並攔 12861。
任一閘不過即回 ``{ok:false, reason}`` **不 emit**。冷送 sniper 已證無效且是鎖帳號
風險,故本版**移除** ``/snipe*``,改手動為主。

myServer(我方 server_id)判定:server_car_join 本身不帶 server_id,而槽的
owner_server_id 需與「我方 server_id」比對才能算 attackable(自己不能打自己)。
myServer 依序取自:(1) request 的 ``my_server`` 參數;(2) 裝置設定
``star_seize_my_server``;(3) 都沒有則視為 0=未知。未知時 attackable 退化為
「owner!=0 && free_end<=serverTime」,並在搶佔閘(queue_type=1)保留伺服器端
「不能搶自己」錯誤碼作為雙保險。

gate = 裝置 backend=='web_h5' 且設有 web_debug_port(不硬編單一 ip)。
"""
import json
import time

from flask import Blueprint, jsonify, render_template, request

import config_manager
from control_panel.shared.auth import _fly_pet_auth, require_device_access

bp = Blueprint("star_seize", __name__)


# --- gate / myServer 共用 ---
def _gate(ip):
    """回傳 (cfg, error_response)。error_response 非 None 時直接 return 之。"""
    cfg = config_manager.get_device_config(ip)
    if cfg.get("backend") != "web_h5" or not cfg.get("web_debug_port"):
        return cfg, (
            jsonify(
                {
                    "status": "error",
                    "message": "此裝置非 web_h5 或未設定 web_debug_port,無法使用星據面板",
                }
            ),
            403,
        )
    return cfg, None


def _resolve_my_server(cfg, raw):
    """myServer 解析:參數優先,其次裝置設定,否則 0(未知)。回傳非負 int。"""
    if raw not in (None, ""):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0
    try:
        return max(0, int(cfg.get("star_seize_my_server") or 0))
    except (TypeError, ValueError):
        return 0


# --- Python 端閘門(可單元測試,不埋在 JS)---
def _evaluate_seize_gate(state, pos, queue_type, my_server):
    """讀 server_car_info 後在 Python 端算搶佔/駐守閘門。

    回 (ok: bool, reason: str|None)。reason ∈
    {'no-state','pos-not-open','empty','own-server','protected','cooldown','truce'}。
    - free_end<=serverTime 即可攻(free_end==0 = 可攻,不是無資料)。
    - attack_cd_end_time>serverTime = 我方死亡冷卻中(輸了~30分),擋。
    - 休戰以台灣時(serverTime+8h)判:hr>=22 || hr<10 擋。
    - queue_type==2(駐守)只驗槽存在,略過外服/保護/冷卻等攻擊專屬閘。
    """
    if not isinstance(state, dict) or state.get("error"):
        return False, "no-state"
    st = state.get("serverTime")
    if st is None:
        return False, "no-state"
    slot = None
    for s in state.get("slots") or []:
        if s.get("pos") == pos:
            slot = s
            break
    if slot is None:
        return False, "pos-not-open"
    owner = slot.get("owner") or 0
    free_end = slot.get("free_end")

    if queue_type == 2:  # 駐守
        if owner == 0:
            return False, "empty"
        return True, None

    # queue_type == 1(搶佔/攻擊)
    if owner == 0:
        return False, "empty"
    if my_server > 0 and owner == my_server:
        return False, "own-server"
    if free_end is None:  # 無資料 → 當保護中(不冒險送)
        return False, "protected"
    if free_end > st:
        return False, "protected"
    attack_cd = state.get("attack_cd_end_time") or 0
    if attack_cd > st:
        return False, "cooldown"
    tw_hr = ((int(st) + 8 * 3600) % 86400) // 3600
    if tw_hr >= 22 or tw_hr < 10:
        return False, "truce"
    return True, None


def _eval_json(res_tuple):
    """解析 _cdp_evaluate 回傳的 (result, err);把 JSON 字串值 parse 成物件。

    回 (obj|value|None, err|None)。
    """
    result, err = res_tuple
    if err:
        return None, err
    inner = (result or {}).get("result", {})
    exc = (result or {}).get("exceptionDetails")
    if exc:
        return None, str(exc)
    if inner.get("type") == "string":
        try:
            return json.loads(inner["value"]), None
        except Exception:
            return inner.get("value"), None
    return inner.get("value"), None


def _err(msg):
    return jsonify({"status": "error", "message": str(msg)}), 500


# --- 注入 JS ---
# 共用 varint protobuf reader(搬自 live 驗證的 rd),回 {field_number: [values]}。

# 讀 4 槽 + 頂層攻/守冷卻:送 12860、攔回應、解 p_server_car_space、配 serverTime。
# 修正:attack_cd_end_time(top#5)/defend_cd_end_time(top#4);休戰以台灣時 +8h;
# free_end==0 仍算可攻(attackable = owner!=0 && free_end<=serverTime && 非本服)。
_STATE_JS = """
(function(){{
  return new Promise(function(resolve){{
    try {{
      function rd(b){{ var i=0; function v(){{var r=0,sh=0;while(true){{var x=b[i++];r|=(x&0x7f)<<sh;if(!(x&0x80))break;sh+=7;}}return r>>>0;}}
        var f={{}}; while(i<b.length){{var tag=v();var fn=tag>>3,wt=tag&7,val;
          if(wt===0)val=v(); else if(wt===2){{var ln=v();val=b.slice(i,i+ln);i+=ln;}} else if(wt===5){{val=b.slice(i,i+4);i+=4;}} else if(wt===1){{val=b.slice(i,i+8);i+=8;}} else break;
          (f[fn]=f[fn]||[]).push(val);}} return f; }}
      var MY = {my};
      var nm = window.netManager;
      if (!nm || !nm._cnet) {{ resolve(JSON.stringify({{error:'netManager 未就緒,請先開啟網頁進入遊戲'}})); return; }}
      var sock = nm._cnet;
      var done = false;
      var origRecv = sock.reciveMsg.bind(sock);
      var myWrap;
      var finish = function(obj){{ if(done) return; done=true; try{{ if(sock.reciveMsg===myWrap) sock.reciveMsg=origRecv; }}catch(e){{}} resolve(JSON.stringify(obj)); }};
      myWrap = function(cmd, body){{
        try {{
          if ((cmd|0)===12860 && !done) {{
            var u = body instanceof Uint8Array ? body : (body && body.buffer ? new Uint8Array(body.buffer, body.byteOffset||0, body.byteLength) : null);
            if (u) {{
              var top = rd(u); var sps = top[1]||[]; var slots=[];
              for (var k=0;k<sps.length;k++) {{
                var f = rd(sps[k]);
                slots.push({{
                  pos:(f[1]&&f[1][0])||0,
                  owner:(f[2]&&f[2][0])||0,
                  is_free:(f[5]&&f[5][0])||0,
                  free_end:(f[6]&&f[6][0])||0,
                  defQ:(f[4]?f[4].length:0),
                  mount_id:(f[8]&&f[8][0])||0
                }});
              }}
              var attackCd = (top[5]&&top[5][0])||0;
              var defendCd = (top[4]&&top[4][0])||0;
              System.import('chunks:///_virtual/TimeUtil.ts').then(function(m){{
                var st = m.default.serverTime;
                for (var j=0;j<slots.length;j++) {{
                  var s=slots[j];
                  s.remaining = s.free_end ? (s.free_end - st) : 0;
                  s.attackable = (s.owner!==0) && (s.free_end<=st) && (MY>0 ? (s.owner!==MY) : true);
                }}
                slots.sort(function(a,b){{return a.pos-b.pos;}});
                var twHr = Math.floor((((st + 8*3600) % 86400)) / 3600);
                finish({{
                  serverTime:st, myServer:(MY>0?MY:null), slots:slots,
                  attack_cd_end_time:attackCd, defend_cd_end_time:defendCd,
                  my_attack_cd_remaining:Math.max(0, attackCd-st),
                  defend_cd_remaining:Math.max(0, defendCd-st),
                  truce:(twHr>=22 || twHr<10), taiwanHour:twHr
                }});
              }}).catch(function(e){{
                for (var j2=0;j2<slots.length;j2++) slots[j2].attackable=null;
                finish({{serverTime:null, myServer:(MY>0?MY:null), slots:slots, attack_cd_end_time:attackCd, defend_cd_end_time:defendCd, timeErr:String(e)}});
              }});
            }}
          }}
        }} catch(e){{}}
        return origRecv(cmd, body);
      }};
      sock.reciveMsg = myWrap;
      nm.send('car_park.server_car_info', {{}});
      setTimeout(function(){{ finish({{error:'server_car_info 逾時無回應'}}); }}, 6000);
    }} catch(e){{ resolve(JSON.stringify({{error:String(e)}})); }}
  }});
}})()
"""

# 讀某槽守隊配置:送 12868 {{pos:N}}、解 defend_queue(#3) 成員。
_OPPONENT_JS = """
(function(){{
  return new Promise(function(resolve){{
    try {{
      function rd(b){{ var i=0; function v(){{var r=0,sh=0;while(true){{var x=b[i++];r|=(x&0x7f)<<sh;if(!(x&0x80))break;sh+=7;}}return r>>>0;}}
        var f={{}}; while(i<b.length){{var tag=v();var fn=tag>>3,wt=tag&7,val;
          if(wt===0)val=v(); else if(wt===2){{var ln=v();val=b.slice(i,i+ln);i+=ln;}} else if(wt===5){{val=b.slice(i,i+4);i+=4;}} else if(wt===1){{val=b.slice(i,i+8);i+=8;}} else break;
          (f[fn]=f[fn]||[]).push(val);}} return f; }}
      function u8s(x){{ try{{ return new TextDecoder('utf-8').decode(x); }}catch(e){{ return ''; }} }}
      var POS = {pos};
      var nm = window.netManager;
      if (!nm || !nm._cnet) {{ resolve(JSON.stringify({{error:'netManager 未就緒,請先開啟網頁進入遊戲'}})); return; }}
      var sock = nm._cnet;
      var done = false;
      var origRecv = sock.reciveMsg.bind(sock);
      var myWrap;
      var finish = function(obj){{ if(done) return; done=true; try{{ if(sock.reciveMsg===myWrap) sock.reciveMsg=origRecv; }}catch(e){{}} resolve(JSON.stringify(obj)); }};
      myWrap = function(cmd, body){{
        try {{
          if ((cmd|0)===12868 && !done) {{
            var u = body instanceof Uint8Array ? body : (body && body.buffer ? new Uint8Array(body.buffer, body.byteOffset||0, body.byteLength) : null);
            if (u) {{
              var top = rd(u); var dq = top[3]||[]; var defenders=[];
              for (var k=0;k<dq.length;k++) {{
                var f = rd(dq[k]);
                var kv=[]; var il=f[6]||[];
                for (var q=0;q<il.length;q++) {{ var e=rd(il[q]); kv.push({{k:(e[1]&&e[1][0]), v:(e[2]&&e[2][0])}}); }}
                defenders.push({{
                  name:(f[3]&&f[3][0]) ? u8s(f[3][0]) : '',
                  server:(f[2]&&f[2][0])||0,
                  queue_index:(f[4]&&f[4][0])||0,
                  attrs_kv:kv
                }});
              }}
              finish({{pos:POS, defenders:defenders}});
            }}
          }}
        }} catch(e){{}}
        return origRecv(cmd, body);
      }};
      sock.reciveMsg = myWrap;
      nm.send('car_park.server_car_queue', {{pos: {pos}}});
      setTimeout(function(){{ finish({{pos:POS, defenders:[], timeout:true}}); }}, 5000);
    }} catch(e){{ resolve(JSON.stringify({{error:String(e)}})); }}
  }});
}})()
"""

# --- /seize orchestration 的分步 JS(照 live 驗證 act_seize.py)---

# Step 1:確保在奇星車場 arena。若不在 ParkingMainView/ParkingCrossServerView 則導航:
#   家園 → 菇菇車位(455,420) → 找車位 → 跨服車位 → 進場(455,278)。
# 若停在某 CrossServerView 則先關掉回 MainView。座標點擊以 canvas DOM MouseEvent 模擬
# (viewport 540x960;Playwright page.mouse.click 已證遊戲吃 DOM 滑鼠事件)。
_NAV_JS = """
(async function(){
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function norm(){ var s=cc.director.getScene(); var ui=(s.children||[]).filter(function(c){return c.name=='UIRoot';})[0]; if(!ui) return []; var nv=(ui.children||[]).filter(function(c){return c.name=='NormalView';})[0]; if(!nv) return []; return nv.children.filter(function(c){return c.active;}).map(function(c){return c.name;}); }
  function emitLabel(t){ var s=cc.director.getScene(); var hit=null; var walk=function(n,d){ if(!n||d>20||!n.active) return; var comps=n._components||[]; for(var i=0;i<comps.length;i++){ var c=comps[i]; if(c&&typeof c.string==='string'&&c.string.trim()===t){ var up=n,b=null; for(var j=0;j<6&&up;j++){ if(up.getComponent&&up.getComponent('cc.Button')){ b=up; break; } up=up.parent; } hit=b||n; } } var ch=n.children||[]; for(var k=0;k<ch.length;k++) walk(ch[k],d+1); }; walk(s,0); if(!hit) return false; try{ hit.emit('click',hit); }catch(e){ return false; } return true; }
  function tapXY(x,y){ var cv=document.querySelector('canvas'); if(!cv) return false; var r=cv.getBoundingClientRect(); var cx=r.left+x, cy=r.top+y; ['mousedown','mouseup','click'].forEach(function(t){ try{ cv.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,clientX:cx,clientY:cy,button:0})); }catch(e){} }); return true; }
  function closeView(){ var s=cc.director.getScene(); var ui=(s.children||[]).filter(function(c){return c.name=='UIRoot';})[0]; if(!ui) return; var nv=(ui.children||[]).filter(function(c){return c.name=='NormalView';})[0]; if(!nv) return; var v=(nv.children||[]).filter(function(c){return c.name=='ParkingCrossServerView';})[0]; if(!v) return; var walk=function(n,d){ if(!n||d>6) return; if(/btnClose|btnBack/i.test(n.name||'')&&n.active){ try{ n.emit('click',n); }catch(e){} } var ch=n.children||[]; for(var k=0;k<ch.length;k++) walk(ch[k],d+1); }; walk(v,0); }
  try{
    var v = norm();
    var inMain = v.indexOf('ParkingMainView')>=0;
    var inCross = v.indexOf('ParkingCrossServerView')>=0;
    if (!inMain && !inCross){
      emitLabel('家園'); await sleep(1500); tapXY(455,420); await sleep(2000);
      emitLabel('找車位'); await sleep(1600); emitLabel('跨服車位'); await sleep(1800);
      tapXY(455,278); await sleep(2500);
    }
    v = norm();
    if (v.indexOf('ParkingCrossServerView')>=0){ closeView(); await sleep(1200); }
    v = norm();
    return JSON.stringify({view:v, inArena: v.indexOf('ParkingMainView')>=0});
  }catch(e){ return JSON.stringify({error:String(e)}); }
})()
"""

# Step 2:tap building1..4,開啟的 ParkingCrossServerView 標題「星據車位N」→ 比對 N==目標 pos。
# 不同 building↔pos 對應靠標題判定;不符即關掉試下一個。回 {opened: pos|null}。
_OPEN_JS = r"""
(async function(){
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function tapBuilding(bn){ var s=cc.director.getScene(); var path=['UIRoot','NormalView','ParkingMainView','scrollMiddleShow','view','content','buildingRoot2','building'+bn]; var n=s; for(var i=0;i<path.length;i++){ if(!n) return false; var ch=n.children||[]; var nx=null; for(var j=0;j<ch.length;j++){ if(ch[j].name===path[i]){ nx=ch[j]; break; } } n=nx; } if(!n) return false; try{ n.emit('click',n); }catch(e){ return false; } return true; }
  function viewPos(){ var s=cc.director.getScene(); var t=null; var walk=function(n,d){ if(!n||d>18||!n.active) return; var comps=n._components||[]; for(var i=0;i<comps.length;i++){ var c=comps[i]; if(c&&typeof c.string==='string'){ var m=/星據車位\s*(\d+)/.exec(c.string); if(m) t=parseInt(m[1]); } } var ch=n.children||[]; for(var k=0;k<ch.length;k++) walk(ch[k],d+1); }; walk(s,0); return t; }
  function closeView(){ var s=cc.director.getScene(); var ui=(s.children||[]).filter(function(c){return c.name=='UIRoot';})[0]; if(!ui) return; var nv=(ui.children||[]).filter(function(c){return c.name=='NormalView';})[0]; if(!nv) return; var v=(nv.children||[]).filter(function(c){return c.name=='ParkingCrossServerView';})[0]; if(!v) return; var walk=function(n,d){ if(!n||d>6) return; if(/btnClose|btnBack/i.test(n.name||'')&&n.active){ try{ n.emit('click',n); }catch(e){} } var ch=n.children||[]; for(var k=0;k<ch.length;k++) walk(ch[k],d+1); }; walk(v,0); }
  try{
    var TARGET = {pos};
    for (var bi=1; bi<=4; bi++){
      if (!tapBuilding(bi)) continue;
      await sleep(1800);
      var vp = viewPos();
      if (vp===TARGET){ return JSON.stringify({opened:TARGET, tried:bi}); }
      closeView(); await sleep(1000);
    }
    return JSON.stringify({opened:null});
  }catch(e){ return JSON.stringify({error:String(e)}); }
})()
""".replace("{pos}", "%POS%")  # placeholder 由 Python 以 %POS% 替換,避免 regex 花括號衝突

# Step 3:讀一份 fresh server_car_info(12860),回原始 {serverTime, attack/defend cd, slots}。
# 閘門判定在 Python 端(_evaluate_seize_gate),不埋 JS。
_GATE_READ_JS = """
(function(){
  return new Promise(function(resolve){
    try {
      function rd(b){ var i=0; function v(){var r=0,sh=0;while(true){var x=b[i++];r|=(x&0x7f)<<sh;if(!(x&0x80))break;sh+=7;}return r>>>0;}
        var f={}; while(i<b.length){var tag=v();var fn=tag>>3,wt=tag&7,val;
          if(wt===0)val=v(); else if(wt===2){var ln=v();val=b.slice(i,i+ln);i+=ln;} else if(wt===5){val=b.slice(i,i+4);i+=4;} else if(wt===1){val=b.slice(i,i+8);i+=8;} else break;
          (f[fn]=f[fn]||[]).push(val);} return f; }
      var nm = window.netManager;
      if (!nm || !nm._cnet) { resolve(JSON.stringify({error:'netManager 未就緒'})); return; }
      var sock = nm._cnet;
      var done = false;
      var origRecv = sock.reciveMsg.bind(sock);
      var myWrap;
      var finish = function(obj){ if(done) return; done=true; try{ if(sock.reciveMsg===myWrap) sock.reciveMsg=origRecv; }catch(e){} resolve(JSON.stringify(obj)); };
      myWrap = function(cmd, body){
        try {
          if ((cmd|0)===12860 && !done) {
            var u = body instanceof Uint8Array ? body : (body && body.buffer ? new Uint8Array(body.buffer, body.byteOffset||0, body.byteLength) : null);
            if (u) {
              var top = rd(u); var sps = top[1]||[]; var slots=[];
              for (var k=0;k<sps.length;k++) { var f=rd(sps[k]); slots.push({pos:(f[1]&&f[1][0])||0, owner:(f[2]&&f[2][0])||0, is_free:(f[5]&&f[5][0])||0, free_end:(f[6]&&f[6][0])||0, defQ:(f[4]?f[4].length:0)}); }
              var attackCd=(top[5]&&top[5][0])||0; var defendCd=(top[4]&&top[4][0])||0;
              System.import('chunks:///_virtual/TimeUtil.ts').then(function(m){
                finish({serverTime:m.default.serverTime, attack_cd_end_time:attackCd, defend_cd_end_time:defendCd, slots:slots});
              }).catch(function(e){ finish({error:'serverTime 讀取失敗: '+String(e)}); });
            }
          }
        } catch(e){}
        return origRecv(cmd, body);
      };
      sock.reciveMsg = myWrap;
      nm.send('car_park.server_car_info', {});
      setTimeout(function(){ finish({error:'server_car_info 逾時,未讀到狀態'}); }, 6000);
    } catch(e){ resolve(JSON.stringify({error:String(e)})); }
  });
})()
"""

# Step 4(僅閘門全過才呼叫):在已開的 view 內 emit「搶佔」(或駐守)tab → emit「加入搶佔」
# (或加入駐守)→ 處理確認框 emit「確定」→ 攔 12861 {code,pos,queue_type,queue_index}。
# 整條只送一次 join。
_JOIN_JS = """
(function(){{
  return new Promise(function(resolve){{
    try {{
      function rd(b){{ var i=0; function v(){{var r=0,sh=0;while(true){{var x=b[i++];r|=(x&0x7f)<<sh;if(!(x&0x80))break;sh+=7;}}return r>>>0;}}
        var f={{}}; while(i<b.length){{var tag=v();var fn=tag>>3,wt=tag&7,val;
          if(wt===0)val=v(); else if(wt===2){{var ln=v();val=b.slice(i,i+ln);i+=ln;}} else if(wt===5){{val=b.slice(i,i+4);i+=4;}} else if(wt===1){{val=b.slice(i,i+8);i+=8;}} else break;
          (f[fn]=f[fn]||[]).push(val);}} return f; }}
      function emitLabel(t){{ var s=cc.director.getScene(); var hit=null; var walk=function(n,d){{ if(!n||d>24||!n.active) return; var comps=n._components||[]; for(var i=0;i<comps.length;i++){{ var c=comps[i]; if(c&&typeof c.string==='string'&&c.string.trim()===t){{ var up=n,b=null; for(var j=0;j<6&&up;j++){{ if(up.getComponent&&up.getComponent('cc.Button')){{ b=up; break; }} up=up.parent; }} hit=b||n; }} }} var ch=n.children||[]; for(var k=0;k<ch.length;k++) walk(ch[k],d+1); }}; walk(s,0); if(!hit) return false; try{{ hit.emit('click',hit); }}catch(e){{ return false; }} return true; }}
      function emitConfirm(){{ var s=cc.director.getScene(); var hit=null; var walk=function(n,d){{ if(!n||d>24||!n.active) return; var comps=n._components||[]; for(var i=0;i<comps.length;i++){{ var c=comps[i]; if(c&&typeof c.string==='string'){{ var t=c.string.trim(); if(t==='確定'||t==='確認'||t==='确定'){{ var up=n,b=null; for(var j=0;j<6&&up;j++){{ if(up.getComponent&&up.getComponent('cc.Button')){{ b=up; break; }} up=up.parent; }} hit=b||n; }} }} }} var ch=n.children||[]; for(var k=0;k<ch.length;k++) walk(ch[k],d+1); }}; walk(s,0); if(!hit) return false; try{{ hit.emit('click',hit); }}catch(e){{ return false; }} return true; }}
      var POS = {pos}, QT = {qt};
      var TAB = QT===1 ? '搶佔' : '駐守';
      var JOIN = QT===1 ? '加入搶佔' : '加入駐守';
      var nm = window.netManager;
      if (!nm || !nm._cnet) {{ resolve(JSON.stringify({{ok:false, error:'netManager 未就緒'}})); return; }}
      var sock = nm._cnet;
      var done = false;
      var origRecv = sock.reciveMsg.bind(sock);
      var myWrap;
      var finish = function(obj){{ if(done) return; done=true; try{{ if(sock.reciveMsg===myWrap) sock.reciveMsg=origRecv; }}catch(e){{}} resolve(JSON.stringify(obj)); }};
      myWrap = function(cmd, body){{
        try {{
          if ((cmd|0)===12861 && !done) {{
            var u = body instanceof Uint8Array ? body : (body && body.buffer ? new Uint8Array(body.buffer, body.byteOffset||0, body.byteLength) : null);
            if (u) {{ var g=rd(u); finish({{ok:true, code:(g[1]&&g[1][0]), pos:(g[2]&&g[2][0]), queue_type:(g[3]&&g[3][0]), queue_index:(g[4]&&g[4][0])}}); }}
          }}
        }} catch(e){{}}
        return origRecv(cmd, body);
      }};
      sock.reciveMsg = myWrap;
      emitLabel(TAB);
      setTimeout(function(){{
        var clicked = emitLabel(JOIN);
        setTimeout(function(){{
          emitConfirm();
          setTimeout(function(){{ if(!done) finish({{ok:true, sent:clicked, code:null, note:'已送出,未攔到 12861 回應'}}); }}, 2500);
        }}, 1200);
      }}, 1200);
    }} catch(e){{ resolve(JSON.stringify({{ok:false, error:String(e)}})); }}
  }});
}})()
"""


# --- page route ---
@bp.route("/star-seize")
@_fly_pet_auth
def star_seize_page():
    """星據搶佔獨立頁(側邊欄切換);template 由前端任務建立。"""
    from control_panel.routes_pages import _get_frontend_version

    return render_template("star_seize.html", frontend_version=_get_frontend_version())


# --- api routes ---
@bp.route("/api/star_seize/state/<ip>", methods=["GET"])
def star_seize_state(ip):
    require_device_access(ip)
    cfg, err = _gate(ip)
    if err:
        return err
    my = _resolve_my_server(cfg, request.args.get("my_server"))
    import control_panel_app as _cpa

    js = _STATE_JS.format(my=my)
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="state", timeout=10)


@bp.route("/api/star_seize/opponent/<ip>", methods=["GET"])
def star_seize_opponent(ip):
    require_device_access(ip)
    _cfg, err = _gate(ip)
    if err:
        return err
    try:
        pos = int(request.args.get("pos"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "pos 需為整數"}), 400
    if pos not in (1, 2, 3, 4):
        return jsonify({"status": "error", "message": "pos 需為 1..4"}), 400

    import control_panel_app as _cpa

    js = _OPPONENT_JS.format(pos=pos)
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="opponent", timeout=8)


@bp.route("/api/star_seize/seize/<ip>", methods=["POST"])
def star_seize_seize(ip):
    """開-view-in-context 送搶佔/駐守。Python 端多步 orchestration:
    ensure arena → open target building → 讀 server_car_info 驗全閘 → 全過才 emit join。
    任一閘不過回 {ok:false, reason} 不 emit;成功回 {ok:true, code,pos,queue_type,queue_index}。
    """
    require_device_access(ip)
    cfg, err = _gate(ip)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    try:
        pos = int(payload.get("pos"))
        queue_type = int(payload.get("queue_type"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "pos/queue_type 需為整數"}), 400
    if pos not in (1, 2, 3, 4):
        return jsonify({"status": "error", "message": "pos 需為 1..4"}), 400
    if queue_type not in (1, 2):
        return jsonify(
            {"status": "error", "message": "queue_type 需為 1(搶佔) 或 2(駐守)"}
        ), 400
    my = _resolve_my_server(cfg, payload.get("my_server"))

    import control_panel_app as _cpa

    # Step 1: 確保在奇星車場 arena
    nav, e = _eval_json(
        _cpa._cdp_evaluate(ip, _NAV_JS, await_promise=True, timeout=25)
    )
    if e:
        return _err(e)
    if isinstance(nav, dict) and nav.get("error"):
        return _err(nav["error"])
    time.sleep(0.3)

    # Step 2: 開啟目標 pos 的 building view(標題比對)
    open_js = _OPEN_JS.replace("%POS%", str(pos))
    opened, e = _eval_json(
        _cpa._cdp_evaluate(ip, open_js, await_promise=True, timeout=30)
    )
    if e:
        return _err(e)
    if isinstance(opened, dict) and opened.get("error"):
        return _err(opened["error"])
    if not isinstance(opened, dict) or opened.get("opened") != pos:
        return jsonify({"status": "ok", "reply": {"ok": False, "reason": "pos-not-open"}})
    time.sleep(0.3)

    # Step 3: 讀 fresh server_car_info + Python 端驗全閘
    state, e = _eval_json(
        _cpa._cdp_evaluate(ip, _GATE_READ_JS, await_promise=True, timeout=10)
    )
    if e:
        return _err(e)
    ok, reason = _evaluate_seize_gate(state, pos, queue_type, my)
    if not ok:
        return jsonify({"status": "ok", "reply": {"ok": False, "reason": reason}})
    time.sleep(0.3)

    # Step 4: 全閘通過才 emit join + confirm,攔 12861
    js = _JOIN_JS.format(pos=pos, qt=queue_type)
    reply, e = _eval_json(_cpa._cdp_evaluate(ip, js, await_promise=True, timeout=12))
    if e:
        return _err(e)
    return jsonify(
        {"status": "ok", "reply": reply if isinstance(reply, dict) else {"ok": True}}
    )
