"""非破壞性 live 驗證：用已開瀏覽器擷取 守護靈/神器 原始封包 → 餵 Python parser + 中文名。

證明「純 WS Python 讀取 + config_names 中文名解析」在真實資料上正確，且**不**另開
WS 連線（不踢使用者手動開的 session）。只讀不寫。

  python tools/verify_inventory_read.py [emulator-5554]
"""
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control_panel.shared.cdp import _cdp_evaluate
from ws_token import artifact_gem as ws_gem
from ws_token import spirit as ws_spirit
from utils import config_names


def _capture_body(device: str, cmd: int) -> bytes:
    """送空 body 的 read cmd，hook reciveMsg 抓回**明文 protobuf** body（base64）。"""
    expr = r"""
new Promise((resolve)=>{
  function done(o){ resolve(o); }
  try{
    const sock=netManager._cnet; const orig=sock.reciveMsg.bind(sock); let cleared=false;
    const cleanup=()=>{ if(!cleared){cleared=true;sock.reciveMsg=orig;} };
    const tid=setTimeout(()=>{cleanup();done('ERR:timeout');},8000);
    sock.reciveMsg=function(c,b){
      if(c===__CMD__ && !cleared){ clearTimeout(tid); cleanup();
        try{ let s=''; for(let i=0;i<b.length;i++) s+=String.fromCharCode(b[i]); done(btoa(s)); }
        catch(e){ done('ERR:'+e); } }
      return orig(c,b); };
    sock.sendMessage(__CMD__, new Uint8Array());
  }catch(e){ done('ERR:'+e); }
})
""".replace("__CMD__", str(cmd))
    result, err = _cdp_evaluate(device, expr, await_promise=True, timeout=15)
    if err:
        raise RuntimeError(f"CDP error: {err}")
    val = result.get("result", {}).get("value")
    if not isinstance(val, str) or val.startswith("ERR:"):
        raise RuntimeError(f"capture cmd={cmd} failed: {val}")
    return base64.b64decode(val)


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else "emulator-5554"
    print(f"== 非破壞性讀取驗證 on {device} ==")
    print("config_names tables:", {k: len(v) for k, v in config_names._tables().items()})

    # 守護靈（中文名是本次重點修正：舊碼取 _data[6] 路徑檔名，新碼取 configLanguage 中文名）
    sp_body = _capture_body(device, ws_spirit.CMD_INFO)
    sp = ws_spirit.parse_spirit_info(sp_body)
    print(f"\n[守護靈] {len(sp.spirits)} 隻 (reshape={sp.reshape_times})")
    for s in sp.spirits[:5]:
        name = config_names.spirit_name(s.config_id)
        affix = next(((config_names.attr_name(k), v) for p in s.positions
                      for k, v in p.cur_attrs.items()), None)
        print(f"  #{s.config_id} {name}  Lv{s.level}  詞條樣本={affix}")

    # 神器附魔石
    gem_body = _capture_body(device, ws_gem.CMD_INFO)
    inv = ws_gem.parse_gem_info(gem_body)
    print(f"\n[神器附魔石] {len(inv.gems)} 顆  tab={inv.tab}  已裝備={len(inv.equipped_ids)}")
    for g in sorted(inv.gems, key=lambda g: (g.quality, g.lv))[:5]:
        suit = config_names.suit_name(g.suit)
        qual = config_names.quality_name(g.quality)
        eq = "裝備中" if g.id in inv.equipped_ids else ""
        base = {config_names.attr_name(k): v for k, v in list(g.base_attr.items())[:2]}
        print(f"  {qual}·{suit}  Lv{g.lv} pos{g.pos} {eq}  主屬={base}")

    # 一致性：中文名應真的解析出來（非純數字）才算過
    spirit_ok = any(config_names.spirit_name(s.config_id) != str(s.config_id)
                    for s in sp.spirits)
    suit_ok = any(config_names.suit_name(g.suit) != str(g.suit) for g in inv.gems)
    qual_ok = any(config_names.quality_name(g.quality) != str(g.quality) for g in inv.gems)
    print(f"\n中文名解析: 守護靈={spirit_ok} 套裝={suit_ok} 品質={qual_ok}")
    ok = spirit_ok and suit_ok and qual_ok and len(inv.gems) > 0 and len(sp.spirits) > 0
    print("== 結果:", "[PASS] 純 WS Python 讀取 + 中文名解析正確" if ok else "[FAIL]", "==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
