# 每帳號挖礦地圖完整記錄 + 回放 設計

使用者需求（2026-07-12）：「未來都記錄 log，每個帳號都會有一份完整的地圖可以回放。」

## 目標

每台裝置（帳號）在挖礦時持久記錄探索到的地圖與動作，事後可以：
1. 重建該帳號迄今探索過的**完整縱向地圖**（累積、跨 session）。
2. 逐步**回放**任一次挖礦 session（盤面、動作、庫存變化）。

## 架構

### 記錄器 `utils/mining_map_recorder.py`

`MiningMapRecorder(device_id, backend)`，路徑一律走 `utils/log_paths.LogPaths`（測試用 `with_root()` 沙箱）：

```
logs/<device>/mining_map/
├── session_YYYYMMDD_HHMMSS.jsonl   # 逐輪事件（一次挖礦 = 一檔）
└── global_map.json                 # 累積全圖（永久保留）
```

JSONL 事件（一行一事件，UTF-8 無 BOM）：

- `{"ev":"start","ts":...,"backend":"adb|web_h5|ws","planner":...,"depth_base":N,"inv":{...}}`
- `{"ev":"round","ts":...,"depth":N,"uncertain":bool,"board":[...7xN 壓縮列...],"below":[...可選 WS 已知列...],"steps":[...計畫步...],"exec":{"ok":bool,"reason":...,"shovels":N,"bombs":N,"drills":N},"inv":{...}}`
- `{"ev":"end","ts":...,"totals":{...}}`

盤面列壓縮：每列一個 6 字元字串，字元對照表放模組常數（例：`.`=可達空氣 `,`=不可達空氣 `d`=dirt `D`=unreachable_dirt `r`=rock `R`=unreachable_rock `P`=pit `p`=unreachable_pit `x`=dug pit `?`=unknown）。對照表必須雙向（記錄/回放共用）。

`global_map.json`：`{"rows": {"<絕對深度列號>": "6字元列"}, "max_depth": N, "updated_at": ...}`。
每輪以 depth 對齊寫入（後寫覆蓋先寫；uncertain 深度的輪次不寫 global，只寫 session）。

失敗絕不影響挖礦主流程：所有記錄呼叫 try/except 吞掉並 log warning 一次。

### 接線點

- CNN/ADB：`miner/mining_service.py` 主迴圈（board 分類後 + exec 後），depth 來自 `DepthTracker`（`last_uncertain` 帶入 uncertain 旗標）。
- WS：`ws_token` 挖礦 supervised 迴圈（21 列已知盤 → `below` 欄位；depth 用 WS baseline，authoritative）。

### 設定與 Dashboard（專案鐵則：不可 config-only）

- 裝置設定 `mining_map_record`（bool，**預設 true** — 使用者要求未來都記錄）。
- `config_manager.py` 正規化白名單加入該 key；`templates/dashboard.html` 裝置編輯面板加 toggle（與 mining planner 下拉同區）。

### 回放 CLI `tools/replay_mining_map.py`

- `--device <id>`：列出該裝置 sessions。
- `--device <id> --session <file>`：終端 ASCII 逐幀回放（每輪印盤面+動作+庫存，`--fps` 控速、`--no-anim` 直接 dump 全部）。
- `--device <id> --map`：印累積 global map（由上而下整條）。

### 保留策略

- session JSONL 沿用 log_paths 的裝置 log purge 習慣但放寬：**90 天**（獨立常數）。
- `global_map.json` 永久保留（體積 ~KB 級）。

## 不做（YAGNI）

- 不做 dashboard 網頁版回放 viewer（後續另開；本輪只做 CLI）。
- 不做跨裝置合併地圖、不做礦點統計分析。
- 不回填歷史 log（只記錄啟用後的資料）。

## 測試（TDD，先寫失敗測試）

- recorder：round 事件寫入/壓縮對照表 round-trip、global_map 深度對齊與覆蓋、uncertain 不寫 global、例外不外洩。
- 回放：fixture session 重建盤面 == 原始輸入；global map dump。
- config：`mining_map_record` 正規化 round-trip（預設 true、字串 "false" 正規化）。
- dashboard template：包含 toggle 控制項 id。
