"""Dump 遊戲 config 中文名表 → data/config_names.json（非破壞性，純 WS 唯讀）。

在 emulator-5554 的遊戲 page 注入 JS，把四類中文名表扁平化成 JSON：

  spirit   守護靈 config_id → 中文名
             來源 configSpirit._data[1] (= configLanguage key) → configLanguage._data[1]
  attr     屬性 id → 中文名
             來源 configAttribute.name getter（fallback _data[1] lang）
  suit     神器附魔石套裝 id(101-107) → 套裝中文名
             來源 configArtifact_gemsets.name getter（= _data[1] lang）
  quality  神器附魔石品質 id(3-8) → 品質中文名
             來源 configArtifact_gemquality._data[7] (= configLanguage key)

欄位語義於 2026-06-19 在 5554 live 驗證（見 tools/probe_config_tables.py）。

用法（repo root）:
    PYTHONUTF8=1 PYTHONPATH=. python tools/dump_config_names.py
    # 或:
    conda run -n mushroom1 python tools/dump_config_names.py
"""
import json
import os
import sys

sys.dont_write_bytecode = True

from control_panel.shared.cdp import _cdp_evaluate

IP = "emulator-5554"
OUT_PATH = os.path.join("data", "config_names.json")


# 注入 JS：四類表全 dump，鍵一律字串，回傳 {spirit,attr,suit,quality}。
_DUMP_JS = r"""
(function(){
  function langName(key){
    try { var o = configLanguage.getDataByKey(key);
      if (o && o._data) return o._data[1]; } catch(e){}
    return null;
  }
  var out = {spirit:{}, attr:{}, suit:{}, quality:{}};

  // --- 守護靈：config_id -> 中文名（_data[1] 是 language key）---------------
  try {
    configSpirit.getDatas().forEach(function(e){
      if (!e || !e._data) return;
      var id = e._data[0];
      var nm = langName(e._data[1]) || String(id);
      out.spirit[String(id)] = nm;
    });
  } catch(e){ out.spirit_err = String(e); }

  // --- 屬性：attr_id -> 中文名（.name getter，fallback _data[1] lang）------
  try {
    configAttribute.getDatas().forEach(function(e){
      if (!e || !e._data) return;
      var id = e._data[0];
      var nm; try { nm = e.name; } catch(_){}
      if (!nm) nm = langName(e._data[1]) || String(id);
      out.attr[String(id)] = nm;
    });
  } catch(e){ out.attr_err = String(e); }

  // --- 套裝：suit id(101-107) -> 套裝中文名（.name getter = _data[1] lang）-
  try {
    configArtifact_gemsets.getDatas().forEach(function(e){
      if (!e || !e._data) return;
      var id = e._data[0];
      var nm; try { nm = e.name; } catch(_){}
      if (!nm) nm = langName(e._data[1]) || String(id);
      out.suit[String(id)] = nm;
    });
  } catch(e){ out.suit_err = String(e); }

  // --- 品質：quality id(3-8) -> 品質中文名（_data[7] 是 language key）-------
  try {
    configArtifact_gemquality.getDatas().forEach(function(e){
      if (!e || !e._data) return;
      var id = e._data[0];
      var nm = langName(e._data[7]) || String(id);
      out.quality[String(id)] = nm;
    });
  } catch(e){ out.quality_err = String(e); }

  return JSON.stringify(out);
})()
"""


def main():
    res, err = _cdp_evaluate(IP, _DUMP_JS, await_promise=False, timeout=20)
    if err:
        print("CDP error:", err)
        sys.exit(1)
    inner = res.get("result", {})
    if res.get("exceptionDetails"):
        print("JS exception:", json.dumps(res["exceptionDetails"], ensure_ascii=False)[:800])
        sys.exit(1)
    if inner.get("type") != "string":
        print("unexpected result:", json.dumps(inner, ensure_ascii=False)[:500])
        sys.exit(1)

    parsed = json.loads(inner["value"])
    # 丟掉內嵌的 *_err 鍵，只保留四類乾淨表
    payload = {k: parsed.get(k, {}) for k in ("spirit", "attr", "suit", "quality")}
    errs = {k: parsed[k] for k in parsed if k.endswith("_err")}

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"wrote {OUT_PATH}")
    for cat in ("spirit", "attr", "suit", "quality"):
        items = list(payload[cat].items())
        sample = ", ".join(f"{k}={v}" for k, v in items[:5])
        print(f"  {cat}: {len(items)} entries  | sample: {sample}")
    if errs:
        print("  WARN js errors:", errs)


if __name__ == "__main__":
    main()
