"""星據車位(奇星車場)搶佔面板後端 route。

跨界車位第一列 4 槽的佇列制佔領戰(server_car_* protocol, car_park module):
- ``server_car_info``  12860 (0x323c) — 讀 4 槽狀態(owner/保護倒數/守隊)。
- ``server_car_queue`` 12868 — 讀某槽守隊首位配置/戰力。
- ``server_car_join``  12861 (0x323d) — 加入搶佔(queue_type=1)/駐守(queue_type=2)。

全部走玩家 live session 的 CDP 注入(``control_panel_app._cdp_json_response`` /
``_cdp_evaluate`` 晚綁定 façade),不另開登入。gate = 裝置 backend=='web_h5' 且
設有 web_debug_port(不硬編單一 ip;使用者明確要求 5554 也能用)。

myServer(我方 server_id)判定:server_car_join 本身不帶 server_id,而槽的
owner_server_id 需與「我方 server_id」比對才能算 attackable(自己不能打自己)。
在頁面內沒有一條「已 live 驗證」的乾淨 role.server_id 讀取路徑(live 工具
arm_sniper.py 是由外部把 1467 當參數傳入),因此本 route 不猜頁面 global——
myServer 依序取自:(1) request 的 ``my_server`` 參數;(2) 裝置設定
``star_seize_my_server``;(3) 都沒有則視為 0=未知。未知時 attackable 退化為
「owner!=0 && free_end<=serverTime」,myServer 回 null,交由前端/使用者確認;
sniper 的「變回本服自動中止」在未知時停用(OWN<=0 時不比對),與伺服器端
「不能搶自己」的錯誤碼形成雙保險。
"""
from flask import Blueprint, jsonify, request

import config_manager
from control_panel.shared.auth import require_device_access

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


# --- 注入 JS(module-level 常數;僅以「已驗證的 int」透過 .format 內插,
#     JS 內字面大括號一律 {{ }} 跳脫,無注入面)---

# 共用 varint protobuf reader(搬自 live 驗證的 arm_sniper.py 的 rd),
# 回傳 {field_number: [values]};varint→number,length-delimited→Uint8Array。

# 讀 4 槽:送 12860、攔回應、解 p_server_car_space、配 serverTime。
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
              System.import('chunks:///_virtual/TimeUtil.ts').then(function(m){{
                var st = m.default.serverTime;
                for (var j=0;j<slots.length;j++) {{
                  var s=slots[j];
                  s.remaining = s.free_end ? (s.free_end - st) : 0;
                  s.attackable = (s.owner!==0) && (s.free_end<=st) && (MY>0 ? (s.owner!==MY) : true);
                }}
                slots.sort(function(a,b){{return a.pos-b.pos;}});
                finish({{serverTime:st, myServer:(MY>0?MY:null), slots:slots}});
              }}).catch(function(e){{
                for (var j2=0;j2<slots.length;j2++) slots[j2].attackable=null;
                finish({{serverTime:null, myServer:(MY>0?MY:null), slots:slots, timeErr:String(e)}});
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
# info_list(#6) 為 p_role_change,逐條 rd 後以 {{k:#1, v:#2}} 原樣回傳
# (欄位語意/label 對照為 TODO;此處只回 raw kv)。
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

# 立即搶佔/駐守 — 安全鐵律(使用者):server_car_join(12861) 是唯一鎖帳號風險,
# 「絕不」未經驗證就送。注入 JS 先讀 fresh server_car_info(12860) 找目標槽,搶佔
# (queue_type=1) 前逐項驗證:MY 已知(>0)、owner!=0(非空槽)、owner!=MY(非本服)、
# free_end<=serverTime(保護已過)、非休戰(serverTime 為 UTC+8,hr>=22||hr<10 擋)。
# 全過才「送一次」server_car_join 並攔 12861 回應;任一項不過即回 {{ok:false,reason}}
# 「不送」。駐守(queue_type=2)只確認槽存在。整條流程只送一次 join。
_SEIZE_JS = """
(function(){{
  return new Promise(function(resolve){{
    try {{
      function rd(b){{ var i=0; function v(){{var r=0,sh=0;while(true){{var x=b[i++];r|=(x&0x7f)<<sh;if(!(x&0x80))break;sh+=7;}}return r>>>0;}}
        var f={{}}; while(i<b.length){{var tag=v();var fn=tag>>3,wt=tag&7,val;
          if(wt===0)val=v(); else if(wt===2){{var ln=v();val=b.slice(i,i+ln);i+=ln;}} else if(wt===5){{val=b.slice(i,i+4);i+=4;}} else if(wt===1){{val=b.slice(i,i+8);i+=8;}} else break;
          (f[fn]=f[fn]||[]).push(val);}} return f; }}
      var POS = {pos}, QT = {qt}, MY = {my};
      var nm = window.netManager;
      if (!nm || !nm._cnet) {{ resolve(JSON.stringify({{error:'netManager 未就緒,請先開啟網頁進入遊戲'}})); return; }}
      var sock = nm._cnet;
      var done = false, got860 = false, stage = 'verify';
      var origRecv = sock.reciveMsg.bind(sock);
      var myWrap;
      var finish = function(obj){{ if(done) return; done=true; try{{ if(sock.reciveMsg===myWrap) sock.reciveMsg=origRecv; }}catch(e){{}} resolve(JSON.stringify(obj)); }};
      myWrap = function(cmd, body){{
        try {{
          var u = body instanceof Uint8Array ? body : (body && body.buffer ? new Uint8Array(body.buffer, body.byteOffset||0, body.byteLength) : null);
          if ((cmd|0)===12860 && stage==='verify' && u) {{
            stage = 'verifying'; got860 = true;
            var top = rd(u); var sps = top[1]||[]; var slot=null;
            for (var k=0;k<sps.length;k++) {{ var f=rd(sps[k]); if((f[1]&&f[1][0])===POS) {{ slot={{owner:(f[2]&&f[2][0])||0, free_end:(f[6]&&f[6][0])||0}}; }} }}
            System.import('chunks:///_virtual/TimeUtil.ts').then(function(m){{
              var st = m.default.serverTime;
              if (!slot) {{ finish({{ok:false, reason:'not-found', msg:'找不到該槽位'}}); return; }}
              var owner = slot.owner, fe = slot.free_end;
              if (QT===1) {{
                if (MY<=0) {{ finish({{ok:false, reason:'unknown-server', msg:'未知我方 server_id,無法驗證,拒送搶佔'}}); return; }}
                if (owner===0) {{ finish({{ok:false, reason:'empty', msg:'空槽不可搶'}}); return; }}
                if (owner===MY) {{ finish({{ok:false, reason:'own-server', msg:'本服持有的槽不可搶(只能駐守)'}}); return; }}
                if (fe && fe>st) {{ finish({{ok:false, reason:'protected', msg:'保護中,尚未可搶', remaining:(fe-st)}}); return; }}
                var hr = Math.floor((st % 86400) / 3600);
                if (hr>=22 || hr<10) {{ finish({{ok:false, reason:'truce', msg:'休戰期(22:00-10:00)不可搶佔'}}); return; }}
              }} else {{
                if (owner===0) {{ finish({{ok:false, reason:'empty', msg:'空槽'}}); return; }}
              }}
              stage = 'sent';
              try {{ nm.send('car_park.server_car_join', {{pos: POS, queue_type: QT}}); }}
              catch(e){{ finish({{ok:false, error:String(e)}}); return; }}
              setTimeout(function(){{ finish({{ok:true, sent:true, code:null, note:'已送出,未攔到 12861 回應'}}); }}, 3000);
            }}).catch(function(e){{ finish({{ok:false, error:'serverTime 讀取失敗: '+String(e)}}); }});
          }} else if ((cmd|0)===12861 && stage==='sent' && u) {{
            var g=rd(u); finish({{ok:true, code:(g[1]&&g[1][0]), pos:(g[2]&&g[2][0]), queue_type:(g[3]&&g[3][0]), queue_index:(g[4]&&g[4][0])}});
          }}
        }} catch(e){{}}
        return origRecv(cmd, body);
      }};
      sock.reciveMsg = myWrap;
      nm.send('car_park.server_car_info', {{}});
      setTimeout(function(){{ if(!got860) finish({{ok:false, reason:'timeout', msg:'server_car_info 逾時,未送搶佔'}}); }}, 6000);
    }} catch(e){{ resolve(JSON.stringify({{error:String(e)}})); }}
  }});
}})()
"""

# 自我校正 sniper(搬自 live 驗證的 arm_sniper.py 最新版,參數化 pos/queue_type/own)。
# 安全鐵律:server_car_join 只送「一次」,且僅在同一組驗證全過才送:
#   有資料(freeEnd/owner/lastSeen 皆非空)→ owner!=本服(OWN>0 才比對,變回本服自動中止)
#   → serverTime>=free_end(保護已過) → 非休戰(hr>=22||hr<10 則 truceWait 不送)
#   → 資料新鮮(距上次原生 poll <=8s;否則節流 1/3s 主動補一次 12860,絕不用舊資料開火)。
# 預設被動:靠遊戲原生 ~6s server_car_info poll(hook 攔到)更新,不主動狂送。
# hook 只掛一次(window.__snHook),攔 12861 存 window.__sn.reply。~1s tick。
_SNIPE_JS = """
(async function(){{
  var POS = {pos}, QT = {qt}, OWN = {my};
  var m = await System.import('chunks:///_virtual/TimeUtil.ts');
  var TU = m.default;
  var s = window.netManager._cnet;
  function rd(b){{ var i=0; function v(){{var r=0,sh=0;while(true){{var x=b[i++];r|=(x&0x7f)<<sh;if(!(x&0x80))break;sh+=7;}}return r>>>0;}}
    var f={{}}; while(i<b.length){{var tag=v();var fn=tag>>3,wt=tag&7,val;
      if(wt===0)val=v(); else if(wt===2){{var ln=v();val=b.slice(i,i+ln);i+=ln;}} else if(wt===5){{val=b.slice(i,i+4);i+=4;}} else if(wt===1){{val=b.slice(i,i+8);i+=8;}} else break;
      (f[fn]=f[fn]||[]).push(val);}} return f; }}
  window.__sn = {{pos:POS, qt:QT, own:OWN, freeEnd:null, owner:null, fired:false, reply:null, aborted:null, firedServer:null, firedLocal:null, lastSeen:null, truceWait:false, err:null}};
  if (!window.__snHook) {{
    var orv = s.reciveMsg.bind(s);
    s.reciveMsg = function(c,b){{
      try{{
        if((c|0)===12860){{ var u=b instanceof Uint8Array?b:(b&&b.buffer?new Uint8Array(b.buffer,b.byteOffset||0,b.byteLength):null);
          if(u){{ var top=rd(u); var sps=top[1]||[]; for(var k=0;k<sps.length;k++){{ var f=rd(sps[k]); var pos=(f[1]&&f[1][0]); if(pos===window.__sn.pos){{ window.__sn.freeEnd=(f[6]&&f[6][0])||0; window.__sn.owner=(f[2]&&f[2][0])||0; window.__sn.lastSeen=TU.serverTime; }} }} }}
        }}
        if((c|0)===12861){{ var u2=b instanceof Uint8Array?b:(b&&b.buffer?new Uint8Array(b.buffer,b.byteOffset||0,b.byteLength):null);
          if(u2){{ var g=rd(u2); window.__sn.reply={{code:(g[1]&&g[1][0]),pos:(g[2]&&g[2][0]),queue_type:(g[3]&&g[3][0]),queue_index:(g[4]&&g[4][0])}}; }} }}
      }}catch(e){{}}
      return orv(c,b);
    }};
    window.__snHook = true;
  }}
  if (window.__snTimer) clearInterval(window.__snTimer);
  window.__snLastProbe = 0;
  window.__snTimer = setInterval(function(){{
    if (window.__sn.fired || window.__sn.aborted) {{ clearInterval(window.__snTimer); return; }}
    var now = TU.serverTime;
    var fe = window.__sn.freeEnd, ow = window.__sn.owner, seen = window.__sn.lastSeen;
    if (!fe || ow == null || seen == null) return;
    if (window.__sn.own > 0 && ow === window.__sn.own) {{ window.__sn.aborted = 'slot became own-server ('+ow+')'; clearInterval(window.__snTimer); return; }}
    if (now < fe) return;
    var hr = Math.floor((now % 86400) / 3600);
    if (hr >= 22 || hr < 10) {{ window.__sn.truceWait = true; return; }}
    window.__sn.truceWait = false;
    var fresh = (now - seen) <= 8;
    if (fresh) {{
      window.__sn.fired = true; window.__sn.firedServer = now; window.__sn.firedLocal = Date.now();
      try {{ window.netManager.send('car_park.server_car_join', {{pos: window.__sn.pos, queue_type: window.__sn.qt}}); }}
      catch(e){{ window.__sn.err = String(e); }}
      clearInterval(window.__snTimer);
    }} else if (now - window.__snLastProbe >= 3) {{
      window.__snLastProbe = now;
      try {{ window.netManager.send('car_park.server_car_info', {{}}); }} catch(e){{}}
    }}
  }}, 1000);
  return JSON.stringify({{armed:true, pos:POS, serverTime:TU.serverTime, freeEnd:window.__sn.freeEnd}});
}})()
"""

# 讀 sniper 狀態(補 remaining = freeEnd - serverTime)。無 int 參數,不 .format。
_SNIPE_STATUS_JS = (
    "(async function(){ var sn = window.__sn || {armed:false}; var rem = null;"
    " try { var m = await System.import('chunks:///_virtual/TimeUtil.ts');"
    " if (sn.freeEnd) rem = sn.freeEnd - m.default.serverTime; } catch(e){}"
    " return JSON.stringify(Object.assign({}, sn, {remaining: rem})); })()"
)

# 取消預約。無 int 參數,不 .format。
_SNIPE_CANCEL_JS = (
    "(function(){ try { if (window.__snTimer) clearInterval(window.__snTimer); } catch(e){}"
    " if (window.__sn) window.__sn.aborted = 'cancelled'; else window.__sn = {aborted:'cancelled'};"
    " return JSON.stringify({cancelled:true}); })()"
)


# --- routes ---
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

    js = _SEIZE_JS.format(pos=pos, qt=queue_type, my=my)
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="reply", timeout=8)


@bp.route("/api/star_seize/snipe/<ip>", methods=["POST"])
def star_seize_snipe(ip):
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

    js = _SNIPE_JS.format(pos=pos, qt=queue_type, my=my)
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="snipe", timeout=10)


@bp.route("/api/star_seize/snipe_status/<ip>", methods=["GET"])
def star_seize_snipe_status(ip):
    require_device_access(ip)
    _cfg, err = _gate(ip)
    if err:
        return err
    import control_panel_app as _cpa

    return _cpa._cdp_json_response(
        ip, _SNIPE_STATUS_JS, await_promise=True, data_key="snipe", timeout=8
    )


@bp.route("/api/star_seize/snipe_cancel/<ip>", methods=["POST"])
def star_seize_snipe_cancel(ip):
    require_device_access(ip)
    _cfg, err = _gate(ip)
    if err:
        return err
    import control_panel_app as _cpa

    return _cpa._cdp_json_response(
        ip, _SNIPE_CANCEL_JS, await_promise=False, data_key="snipe", timeout=6
    )
