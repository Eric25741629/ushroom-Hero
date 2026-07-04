"""偵察探針：列出遊戲 page 內所有 config* 表 + 守護靈/屬性/套裝/品質欄位語義。

非破壞性。只讀 config 表 + hook reciveMsg 收一個守護靈 0x4D01 回包拿一個 config_id
當樣本去查 configSpirit.getDataByKey(cfg)，確認哪個欄位是中文名。

用法:
    conda run -n mushroom1 python tools/probe_config_tables.py
"""
import json
import sys

sys.dont_write_bytecode = True

from control_panel.shared.cdp import _cdp_evaluate

IP = "emulator-5554"


# --- step A: 列出全部 config 表名 + 每個 config 物件可用的列舉法 -----------------
_LIST_CONFIG_JS = r"""
(function(){
  var out = {};
  out.config_keys = Object.keys(window).filter(function(k){ return /^config/i.test(k); });
  // 找出帶 datas/getDataByKey 的 config 表（可枚舉的）
  out.tables = {};
  out.config_keys.forEach(function(k){
    try {
      var o = window[k];
      if (!o) return;
      var info = {};
      info.has_datas = (o.datas !== undefined);
      info.has_getDataByKey = (typeof o.getDataByKey === 'function');
      if (o.datas) {
        var dk = Object.keys(o.datas);
        info.datas_count = dk.length;
        info.first_key = dk[0];
      }
      out.tables[k] = info;
    } catch(e){ out.tables[k] = {error: String(e)}; }
  });
  return JSON.stringify(out);
})()
"""


# --- step B: 撈一個守護靈 config_id 樣本 + 解析 configSpirit 一筆 entry 結構 -------
_SPIRIT_SAMPLE_JS = r"""
new Promise(function(resolve){
  function readVarint(buf, off){ var v=0n, s=0n;
    while(true){ var b=buf[off++]; v |= BigInt(b & 0x7f) << s; if(!(b & 0x80)) break; s += 7n; }
    return [v, off]; }
  function walk(buf){ var out=[]; var off=0;
    while(off < buf.length){
      var r=readVarint(buf, off); off=r[1]; var t=Number(r[0]); var f=t>>3, w=t&7;
      if(w===0){ var r2=readVarint(buf, off); off=r2[1]; out.push({f:f,w:0,v:r2[0]}); }
      else if(w===2){ var r2=readVarint(buf, off); off=r2[1]; var L=Number(r2[0]);
        out.push({f:f,w:2,v:buf.slice(off, off+L)}); off+=L; }
      else if(w===1){ out.push({f:f,w:1,v:0n}); off+=8; }
      else if(w===5){ out.push({f:f,w:5,v:0n}); off+=4; }
      else { break; } }
    return out; }
  function num(v){ return (typeof v==='bigint') ? Number(v) : v; }
  function dumpEntry(o){
    // 把 config entry 的可見資訊扁平化，重點看 _data 陣列與頂層 name 屬性
    var res = {};
    try { res.own_keys = Object.keys(o).slice(0, 40); } catch(e){}
    try { if (o._data) res._data = o._data.map(function(x){ return (x===null||x===undefined)? x : (typeof x==='object'? '[obj]' : x); }); } catch(e){}
    try { res.name_prop = o.name; } catch(e){}
    return res;
  }
  try {
    var sock = netManager._cnet;
    var orig = sock.reciveMsg.bind(sock); var cleared=false;
    function cleanup(){ if(!cleared){cleared=true; sock.reciveMsg=orig;} }
    var tid=setTimeout(function(){ cleanup(); resolve(JSON.stringify({error:'timeout spirit'})); }, 8000);
    sock.reciveMsg=function(c,b){
      if(c===19713 && !cleared){ clearTimeout(tid); cleanup();
        try{
          var top=walk(b); var cfgs=[];
          for(var i=0;i<top.length;i++){ var x=top[i];
            if(x.f===5 && x.w===2){ var d=walk(x.v);
              for(var j=0;j<d.length;j++){ if(d[j].f===2 && d[j].w===0){ cfgs.push(num(d[j].v)); } } } }
          var sampleCfg = cfgs.length ? cfgs[0] : null;
          var res = {sample_cfgs: cfgs.slice(0,8), spirit_count: cfgs.length};
          if (sampleCfg !== null) {
            try {
              var e = configSpirit.getDataByKey(sampleCfg);
              res.sample_cfg = sampleCfg;
              res.spirit_entry = dumpEntry(e);
            } catch(err){ res.spirit_entry_err = String(err); }
          }
          resolve(JSON.stringify(res));
        }catch(e){ resolve(JSON.stringify({error:String(e)+(e.stack||'')})); } }
      return orig(c,b); };
    sock.sendMessage(19713, new Uint8Array());
  } catch(e){ resolve(JSON.stringify({error:String(e)})); }
})
"""


# --- step C: configAttribute 一筆 + 找套裝/品質表 ------------------------------
_ATTR_SUIT_QUALITY_JS = r"""
(function(){
  var out = {};
  function dumpFirst(tableName, n){
    try {
      var o = window[tableName];
      if (!o || !o.datas) return {missing:true};
      var keys = Object.keys(o.datas);
      var samples = [];
      for (var i=0; i<Math.min(n||3, keys.length); i++){
        var k = keys[i];
        var e = o.getDataByKey ? o.getDataByKey(k) : o.datas[k];
        var rec = {key:k};
        try { rec._data = e._data ? e._data.map(function(x){ return (x===null||x===undefined)? x : (typeof x==='object'?'[obj]':x); }) : undefined; } catch(_){}
        try { rec.own_keys = Object.keys(e).slice(0,30); } catch(_){}
        try { rec.name = e.name; } catch(_){}
        samples.push(rec);
      }
      return {count: keys.length, first_keys: keys.slice(0,8), samples: samples};
    } catch(e){ return {error:String(e)}; }
  }
  // configAttribute
  out.configAttribute = dumpFirst('configAttribute', 3);
  // 掃所有 config 表名找 suit/quality/artifact/equip 相關
  var allCfg = Object.keys(window).filter(function(k){ return /^config/i.test(k); });
  out.candidate_tables = allCfg.filter(function(k){
    return /suit|quality|artifact|gem|equip|品|set/i.test(k);
  });
  // 對每個候選表 dump first
  out.candidates = {};
  out.candidate_tables.forEach(function(k){ out.candidates[k] = dumpFirst(k, 4); });
  // 也把所有可枚舉 config 表名 + 筆數列出，幫忙人工找
  out.all_tables_brief = {};
  allCfg.forEach(function(k){
    try { var o = window[k];
      out.all_tables_brief[k] = (o && o.datas) ? Object.keys(o.datas).length : null;
    } catch(e){ out.all_tables_brief[k] = 'err'; }
  });
  return JSON.stringify(out);
})()
"""


def run(label, expr, await_promise=False):
    res, err = _cdp_evaluate(IP, expr, await_promise=await_promise, timeout=20)
    print("=" * 70)
    print(f"[{label}]")
    if err:
        print("  ERROR:", err)
        return None
    inner = res.get("result", {})
    exc = res.get("exceptionDetails")
    if exc:
        print("  EXCEPTION:", json.dumps(exc, ensure_ascii=False)[:800])
        return None
    val = inner.get("value")
    if inner.get("type") == "string":
        try:
            parsed = json.loads(val)
            print(json.dumps(parsed, ensure_ascii=False, indent=2)[:6000])
            return parsed
        except Exception:
            print("  raw:", str(val)[:2000])
            return val
    print("  value:", json.dumps(val, ensure_ascii=False)[:2000])
    return val


# --- step D: 攔截 sendMessage 拿 artifact_gem 各動作真實 cmd 號（不傳送）---------
_CMD_CODES_JS = r"""
(function(){
  var sock = netManager._cnet;
  var realSend = sock.sendMessage.bind(sock);
  var cap = {};
  sock.sendMessage = function(code, buf){ cap.code = code; cap.len = (buf&&buf.length)||0; };
  var names = [
    'artifact_gem.artifact_gem_info_c2s',
    'artifact_gem.artifact_gem_split_c2s',
    'artifact_gem.artifact_gem_sub_c2s',
    'artifact_gem.artifact_gem_up_c2s',
    'artifact_gem.artifact_gem_lock_c2s',
    'artifact_gem.artifact_gem_wear_c2s',
    'spirit.spirit_info_c2s'
  ];
  var codes = {};
  names.forEach(function(nm){
    cap = {};
    try {
      var body = {};
      if (nm.indexOf('split') >= 0) body = {cost_list: []};
      else if (nm.indexOf('sub') >= 0) body = {gem_id: 0};
      else if (nm.indexOf('up') >= 0) body = {gem_id: 0, use_list: [], item_list: [], lock_attr: 0};
      else if (nm.indexOf('lock') >= 0) body = {gem_id: 0};
      else if (nm.indexOf('wear') >= 0) body = {pos_id: 0, gem_id: 0, tab: 0};
      netManager.send(nm, body);
      codes[nm] = {code: cap.code, hex: cap.code != null ? '0x' + cap.code.toString(16) : null, len: cap.len};
    } catch(e){ codes[nm] = {error: String(e)}; }
  });
  sock.sendMessage = realSend;   // 還原，全程未真正傳送
  return JSON.stringify(codes);
})()
"""


# --- step E: protoRoot 拿 c2s/p_artifact_gem 權威 field 定義 --------------------
_PROTO_FIELDS_JS = r"""
(function(){
  var root = netManager.protoRoot;
  function fields(typeName){
    try { var T = root.lookupType(typeName); var f = {};
      for (var k in T.fields){ var fld = T.fields[k];
        f[fld.id] = {name: k, type: fld.type, rule: fld.rule || 'optional'}; }
      return f;
    } catch(e){ return {error: String(e)}; }
  }
  var out = {};
  ['type.p_artifact_gem',
   'artifact_gem.artifact_gem_split_c2s',
   'artifact_gem.artifact_gem_sub_c2s',
   'artifact_gem.artifact_gem_up_c2s',
   'artifact_gem.artifact_gem_lock_c2s',
   'artifact_gem.artifact_gem_info_s2c'].forEach(function(n){ out[n] = fields(n); });
  return JSON.stringify(out);
})()
"""


def main():
    run("A: config table list", _LIST_CONFIG_JS)
    run("B: spirit sample + configSpirit entry", _SPIRIT_SAMPLE_JS, await_promise=True)
    run("C: configAttribute + suit/quality candidates", _ATTR_SUIT_QUALITY_JS)
    run("D: artifact_gem real cmd codes (intercept, no transmit)", _CMD_CODES_JS)
    run("E: protoRoot field defs (p_artifact_gem + c2s)", _PROTO_FIELDS_JS)


if __name__ == "__main__":
    main()
