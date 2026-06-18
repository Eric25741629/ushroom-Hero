"""5554 live 受控測試：分解(賣)剛好一顆安全的神器附魔石，驗證 0x350A 協議。

走**已開瀏覽器**的 netManager._cnet（CDP 注入），不另開純 WS 連線（避免異地登入
踢掉使用者手動開的 session）。送出的 body 就是 ws_token.artifact_gem.build_split_body
的產出，等於同時驗證 Python 端編碼。

  python tools/live_test_gem_split.py [emulator-5554]

只分解一顆：未鎖、未裝備(不在 tab_list)、等級<7、最低品質。會真的消耗一顆石。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control_panel.shared.cdp import _cdp_evaluate
from ws_token import artifact_gem as ag


def _cdp(device: str, expr: str):
    """跑一段回傳 JSON 字串的 JS（Promise），解析成 Python 物件。"""
    result, err = _cdp_evaluate(device, expr, await_promise=True, timeout=15)
    if err:
        raise RuntimeError(f"CDP error: {err}")
    inner = result.get("result", {})
    if result.get("exceptionDetails"):
        raise RuntimeError(f"JS exception: {result['exceptionDetails']}")
    val = inner.get("value")
    if inner.get("type") == "string":
        return json.loads(val)
    return val


# 讀 0x3501 → 算 equipped 集合 → 選一顆安全候選 + 前置計數
_PICK_JS = r"""
new Promise((resolve)=>{
  function done(o){ resolve(JSON.stringify(o)); }
  function rv(buf,off){ let v=0n,s=0n; while(true){ const b=buf[off++]; v|=BigInt(b&0x7f)<<s; if(!(b&0x80))break; s+=7n;} return [v,off]; }
  function walk(buf){ const out=[]; let off=0; while(off<buf.length){ let r=rv(buf,off);off=r[1];const t=Number(r[0]);const f=t>>3,w=t&7;
    if(w===0){let r2=rv(buf,off);off=r2[1];out.push({f,w,v:r2[0]});}
    else if(w===2){let r2=rv(buf,off);off=r2[1];const L=Number(r2[0]);out.push({f,w,v:buf.slice(off,off+L)});off+=L;}
    else if(w===1){out.push({f,w,v:0n});off+=8;} else if(w===5){out.push({f,w,v:0n});off+=4;} else break; } return out; }
  function num(v){ return typeof v==='bigint'?v:BigInt(v); }
  try{
    const sock=netManager._cnet; const orig=sock.reciveMsg.bind(sock); let cleared=false;
    const cleanup=()=>{ if(!cleared){cleared=true;sock.reciveMsg=orig;} };
    const tid=setTimeout(()=>{cleanup();done({error:'timeout 0x3501'});},8000);
    sock.reciveMsg=function(c,b){ if(c===13569 && !cleared){ clearTimeout(tid); cleanup();
      try{
        const top=walk(b); const gems=[]; const equipped=new Set();
        for(const x of top){
          if(x.f===2&&x.w===2){ const d=walk(x.v); const g={id:'0',q:0,pos:0,suit:0,lv:0,lock:0};
            for(const y of d){ if(y.f===1)g.id=num(y.v).toString(); else if(y.f===2)g.q=Number(y.v);
              else if(y.f===3)g.pos=Number(y.v); else if(y.f===4)g.suit=Number(y.v);
              else if(y.f===5)g.lv=Number(y.v); else if(y.f===8)g.lock=Number(y.v); }
            gems.push(g); }
          else if(x.f===4&&x.w===2){ for(const t of walk(x.v)){ if(t.f===3&&t.w===2){
            const kv=walk(t.v); let v=0n; for(const z of kv){ if(z.f===2) v=num(z.v); }
            if(v!==0n) equipped.add(v.toString()); } } }
        }
        const locked=gems.filter(g=>g.lock).length;
        // 候選：未鎖 + 未裝備 + lv<7，依 (品質,等級) 由低到高取第一顆
        const cand=gems.filter(g=>!g.lock && !equipped.has(g.id) && g.lv<7)
                       .sort((a,b)=> a.q-b.q || a.lv-b.lv);
        done({ total:gems.length, equipped:equipped.size, locked:locked,
               candidate: cand.length? cand[0] : null,
               cand_count: cand.length });
      }catch(e){ done({error:String(e)+(e.stack||'')}); } }
      return orig(c,b); };
    sock.sendMessage(13569, new Uint8Array());
  }catch(e){ done({error:String(e)}); }
})
"""

# 送 0x350A（body 由 Python 帶入 BODY_INTS），等回應 13578(成功) 或 0x0201(失敗)
_SPLIT_JS_TMPL = r"""
new Promise((resolve)=>{
  function done(o){ resolve(JSON.stringify(o)); }
  function rv(buf,off){ let v=0n,s=0n; while(true){ const b=buf[off++]; v|=BigInt(b&0x7f)<<s; if(!(b&0x80))break; s+=7n;} return [v,off]; }
  function walk(buf){ const out=[]; let off=0; while(off<buf.length){ let r=rv(buf,off);off=r[1];const t=Number(r[0]);const f=t>>3,w=t&7;
    if(w===0){let r2=rv(buf,off);off=r2[1];out.push({f,w,v:r2[0]});}
    else if(w===2){let r2=rv(buf,off);off=r2[1];const L=Number(r2[0]);out.push({f,w,v:buf.slice(off,off+L)});off+=L;}
    else if(w===1){off+=8;} else if(w===5){off+=4;} else break; } return out; }
  try{
    const body=new Uint8Array(__BODY_INTS__);
    const sock=netManager._cnet; const orig=sock.reciveMsg.bind(sock); let cleared=false;
    const cleanup=()=>{ if(!cleared){cleared=true;sock.reciveMsg=orig;} };
    const tid=setTimeout(()=>{cleanup();done({ok:false,reason:'timeout split'});},8000);
    sock.reciveMsg=function(c,b){
      if((c===13578||c===0x0201) && !cleared){ clearTimeout(tid); cleanup();
        if(c===0x0201){ const d=walk(b); let code=0; for(const x of d){ if(x.f===1)code=Number(x.v);} done({ok:false,reason:'rejected',code}); }
        else { const removed=[]; for(const x of walk(b)){ if(x.f===1&&x.w===0) removed.push(x.v.toString()); } done({ok:true,removed}); }
      }
      return orig(c,b); };
    sock.sendMessage(13578, body);
  }catch(e){ done({ok:false,reason:String(e)}); }
})
"""


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else "emulator-5554"
    print(f"== live gem split test on {device} ==")

    before = _cdp(device, _PICK_JS)
    if before.get("error"):
        print("讀 0x3501 失敗:", before["error"]); return 1
    print(f"前置: 總 {before['total']} 顆, 已裝備 {before['equipped']}, 鎖定 {before['locked']}, "
          f"安全候選 {before['cand_count']} 顆")
    cand = before.get("candidate")
    if not cand:
        print("沒有符合 (未鎖+未裝備+lv<7) 的候選，停止。"); return 1
    cid = int(cand["id"])
    print(f"選中: id={cid} 品質={cand['q']} 等級={cand['lv']} 套裝={cand['suit']} pos={cand['pos']}")

    body = ag.build_split_body([cid])
    body_ints = list(body)
    print(f"build_split_body bytes = {body_ints}")

    res = _cdp(device, _SPLIT_JS_TMPL.replace("__BODY_INTS__", json.dumps(body_ints)))
    print("分解回應:", res)
    if not res.get("ok"):
        print("分解未成功（協議可能需改 packed 編碼，見 build_split_body 註解）。")
        return 1
    if str(cid) not in [str(x) for x in res.get("removed", [])]:
        print(f"警告: 回應 removed 不含選中的 id {cid}: {res.get('removed')}")

    after = _cdp(device, _PICK_JS)
    print(f"後置: 總 {after['total']} 顆, 已裝備 {after['equipped']}, 鎖定 {after['locked']}")

    ok = (after["total"] == before["total"] - 1
          and after["equipped"] == before["equipped"]
          and after["locked"] == before["locked"])
    print("== 結果:", "[PASS] 分解一顆、裝備/鎖定不變" if ok else "[FAIL] 不符預期", "==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
