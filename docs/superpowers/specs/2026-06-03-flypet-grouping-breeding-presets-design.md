# 飛寵種類分組 + 配種方案系統 — 設計

日期：2026-06-03
範圍：control panel 飛寵頁（`templates/fly_pet.html`）+ 後端飛寵 API（`control_panel_app.py`）
驗證帳號：`7fe98fc6`（web_h5，`auth_state/7fe98fc6.json`）

## 目標

1. 飛寵列表依「種類」分組，同種類聚在一起、可收合。
2. 自動配種引入「方案」概念：一組命名的完整配種策略，可建多個；每個繁殖巢穴選用一個方案。
3. 方案可限制「種類」（白名單，只從指定種類挑父母）與「詞條」（白名單，父母須同時包含全部）。

## 背景（現況）

- 飛寵列表：`fly_pet.html` 主表依 `quality → level` 排序，無分組。每筆資料已帶 `config_id` / `display_name`（種類），來源 `control_panel_app.py:2101-2115`。
- 自動配種：per-巢穴 `abConfig[homeId]` 存 `{enabled, mode, quality, min_count, min_total_entries, prefer_low_gen}`（`fly_pet.html` `renderAbUI`/`abCfgChange`）。`autoBreedTick` → `processAbSlot` → `POST /api/fly_pet_find_pair`（`control_panel_app.py:2570`）依 criteria 挑最佳兩隻當 `fly_a`/`fly_b`，`base_id` = 巢穴 id。
- config 表枚舉：cocos config 基底類別有 `this.datas`（全部資料陣列）；`getDataByKey` 走 `mapBykey[mainKey]`（minified `index.966f5.js:3781`）。故 `configFly.datas` = 全種類目錄、`configFly_entry.datas` = 全詞條目錄。

## 設計

### Part 1 — 目錄 API

新增 `GET /api/fly_pet_catalog/<ip>`（`@_fly_pet_auth`），CDP evaluate：

- 種類：`configFly.datas` → `[{id, name}]`
- 詞條：`configFly_entry.datas`，依 `id` 去重（同一詞條多等級折疊成一筆，保留 `name`/`quality`）→ `[{id, name, quality}]`

回傳 `{species: [...], entries: [...]}`。前端進飛寵頁時抓一次，存記憶體快取，供方案編輯下拉使用。

容錯：若 `configFly`/`configFly_entry` 或 `.datas` 不存在，回 `{species: [], entries: [], error: "..."}`，前端方案編輯改用「目前擁有飛寵 union」降級填充。

### Part 2 — 列表種類分組

改 `fly_pet.html` 主列表 render：

- 依 `config_id` 分組。每組一個可點擊標題列：種類名 + 數量（例「月光精靈 (5)」），點擊展開/收合。
- 組排序：該組最高品質的飛寵越強排越前；並列時比種類名（穩定）。
- 組內維持現有 `quality → level` 排序。
- 收合狀態存 `localStorage`（key 含 config_id）。重繪保留狀態。
- 後端不動（資料已含 `config_id`/`display_name`）。

### Part 3 — 配種方案系統

**方案資料模型**（存 localStorage，獨立於 `abConfig`）：

```
{
  id: <string, 穩定唯一>,
  name: <string>,
  species_whitelist: [config_id, ...],   // 空 = 不限種類
  entry_whitelist:   [entry_id, ...],    // 空 = 不限詞條；非空 = AND（須全包含）
  mode: 'quality_count' | 'total_count',
  quality: <int>,
  min_count: <int>,
  min_total_entries: <int>,
  prefer_low_gen: <bool>
}
```

- **方案管理 UI**：方案清單（新增 / 命名 / 編輯 / 刪除）。編輯表單：
  - 「限種類」多選、「限詞條」多選（由 Part 1 目錄填充）
  - 既有控制項：mode、品質、最小數量、優先低代數
  - 存 `abPresets`（array）於 localStorage。
- **巢穴改選方案**：每巢穴卡片移除原 inline 條件控制項，改為「方案」下拉（`abConfig[homeId].preset_id`）+ 啟用勾選。各巢穴互不影響。
- **執行**：`processAbSlot` 取巢穴 `preset_id` → 查 `abPresets` → 組 criteria（含 `species_whitelist`/`entry_whitelist`）→ `find_pair`。找不到方案 → 該巢穴標記「未選方案」並跳過。

**後端 `find_pair` 擴充**（`control_panel_app.py:2570` 端點 + 內嵌 JS）：

- criteria 新增 `species_whitelist`、`entry_whitelist`（皆預設空陣列）。
- 在現有 `matches` 篩選後加：
  - `species_whitelist` 非空 → 要求 `pet.config_id` ∈ 白名單。
  - `entry_whitelist` 非空 → 要求候選 entry id 集合涵蓋白名單**全部**（AND）。

**舊設定遷移**：頁面載入時若 `abConfig` 含舊 inline 條件且 `abPresets` 為空，為每個巢穴現有條件各建一個方案（命名「舊設定 #<巢穴id>」），並把該巢穴 `preset_id` 指向它，避免既有設定遺失。

## 測試

TDD，沿用 `tests/test_fly_pet_*.py` 風格，先寫失敗測試：

- `find_pair`：種類白名單過濾（只回符合 config_id）、詞條 AND 過濾（缺一不入選）、空白名單不過濾。
- 目錄端點：回傳 `{species, entries}` 結構；entries 依 id 去重。
- 前端可抽出的純函式（分組排序、方案→criteria 轉換、舊設定遷移）若可獨立測則加測。

## Live 驗證（帳號 7fe98fc6）

1. CDP attach → 確認 `configFly.datas` / `configFly_entry.datas` 可讀，目錄含「月光精靈 / 達摩 / 旅行水母」。
2. 飛寵頁分組顯示正常、收合可用、組排序符合「最高品質在上」。
3. 建一個帶種類 + 詞條限制的方案、套到某巢穴 → `find_pair` 只挑到符合該種類且含全部指定詞條的父母。

## 非目標（YAGNI）

- 不改 ADB 後端（飛寵頁為 web 專用 dashboard）。
- 不做方案匯入/匯出、不做跨裝置同步（localStorage per browser 即可）。
- 不改基底 `base_id` 選擇邏輯（仍 = 巢穴 id）。
