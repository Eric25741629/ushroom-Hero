# 飛寵「手選收藏群組」功能設計 (FLYPET_GROUPS_DESIGN)

> Phase 3 task #36。實作就緒的設計稿。READ-ONLY 調研產物，不改任何程式碼。
> 目標：讓使用者建立**命名、持久化**的飛寵群組（收藏1 / 收藏2 / …），每組可**混合**自己家裡的飛寵（own）與搭檔飛寵（partner），
> 繁殖時「指定槽位從收藏N挑兩隻配」（手選或隨機自動挑），不必每次在 300+ 隻裡翻找。
>
> 與既有機制的區隔（務必在 UI 文案釐清）：
> - **配種方案 (preset)** = 條件 preset（品質/詞條/種類白名單 + 搜尋演算法），不是手點的成員名單。see `templates/fly_pet.html:1951-2123`。
> - **設為基底/A/B** = 一次性 form-fill（寫入 `#bBase`/`#bFlyA`/`#bFlyB`），不持久。see `templates/fly_pet.html:1463-1465`, `setBreedPet` at `:1721`。
> - **遊戲內圖鑑「收藏」(`is_collected`)** = `collection_list` 旗標（保護不被分解），與本功能無關。本功能 UI 一律標「**手選飛寵收藏**」避免混淆。see `fly_pet_list` JS `is_collected` at `routes_fly_pet.py:118`。

---

## 1. 繁殖機制（程式碼實證）

### 1.1 base / A / B 語意

巢穴（home）= 一個繁殖巢。`fly_pet_breed_info` 把每個 home 讀成 `{id, state, fly_a, fly_b, fly_pet, end_time}`（`routes_fly_pet.py:336-386`）：

- `fly_a` / `fly_b` = 投入的**兩隻親代**（parents）。
- `fly_pet` = 結果（後代）。`loadBreed` 顯示為「結果」（`templates/fly_pet.html:1782`）。
- `state` 機器：`0=閒置 1=孵化中 2=可領取 3=待孵化`（`loadBreed` stateMap `:1765`；auto-breed `processAbSlot` `:2312-2377`）。

繁殖三個輸入欄是 `#bBase`（巢穴 id）/ `#bFlyA`（親代 A id）/ `#bFlyB`（親代 B id），see `templates/fly_pet.html:653-657`。
所以使用者要的「指定槽位1從收藏1挑**兩隻**出來配」= **挑兩隻親代（A + B）填入 `#bFlyA` / `#bFlyB`**（base = 巢穴 id，由「載入繁殖池」後從 breed-slot 取得，不是從群組挑）。

### 1.2 `send_66_27` 簽名

```
fly_pet_breed_start(ip)  POST {base_id, fly_a_id, fly_b_id}   # routes_fly_pet.py:483-526
  -> IS(ISInclude.FlyPetControl).send_66_27(base_id, fly_a_id, fly_b_id)   # :500
```

三個都是**整數 pet id**（`int(base_id)` / `int(fly_a_id)` / `int(fly_b_id)`，`:495,:500`）。沒有獨立的「partner 借用槽」參數出現在這條 RPC。
無確認 *Back 事件，所以伺服器送出後輪詢 `home_list[base_id].state` 變 1 為成功（`:499-525`）。

### 1.3 partner 能不能當親代？— 關鍵設計問題的裁決

**裁決：未能由程式碼證實（needs live）。設計採「安全解讀」。**

證據盤點：
1. **支持「可以」**：`fly_pet_breed_info` 對每個 home 的 `fly_a`/`fly_b` 讀出 `role_id: p.role_id || 0`（`routes_fly_pet.py:352`）。
   親代物件本身帶 `role_id` 欄位 → 強烈暗示一個巢穴的親代槽**可以是借來的搭檔飛寵**（own pet 的 `role_id` 為 0/自己）。
2. **支持「不確定」**：`send_66_27` 只吃三個 id，沒有「這個 id 屬於哪個 role」的旁路欄位（`:500`）。
   搭檔飛寵也有 `info.id`（`fly_pet_partner` 讀 `p.info.id`，`routes_fly_pet.py:464`）。若 partner pet 的 `id` 在全域唯一、且伺服器能從 id 反查 owner，則直接把 partner 的 `info.id` 當 `fly_a_id`/`fly_b_id` 傳入**可能**就成立；但這條路徑現有程式從未走過。
3. **現況：own-only**。`find_pair`（`routes_fly_pet.py:736` 起）只掃 `cache.pet_list`（own）；`processAbSlot` 只用 `result.pair.fly_a/fly_b`（own）；手動 `setBreedPet` 只接受 gallery 內的 own `pet.id`（`templates/fly_pet.html:1721-1730`）。partner 區（`doLoadAllPartners` `:1880`）是**唯讀表格**，沒有任何「設為親代」按鈕。

> **如何 live 驗證**（Phase 5）：在一台手動接管的裝置上，用 `fly_pet_partner(role_id)` 取一隻**可用**（`state===0`）的搭檔飛寵 `info.id`，
> 連同一隻自己的 own pet 與一個閒置巢穴，呼叫 `fly_pet_breed_start{base_id, fly_a_id=<own>, fly_b_id=<partner.info.id>}`，
> 觀察 `home_list[base].state` 是否轉 1（成功）或被伺服器拒（state 不變 / 報錯）。
> 若成功 → partner 可直接當親代（id-only）。若失敗 → partner 借用需要另一條 RPC（本設計不涵蓋；保留 group 內 partner 成員為「候選/標記」用途）。

**安全解讀（本設計預設行為）：**
- 群組成員**可以**同時含 own 與 partner（資料模型完整支援，使用者要的混合需求成立）。
- 「填入 A/B」「隨機挑 A/B」**預設只從群組裡的 own 成員挑**，partner 成員在挑選器中可見但標記「搭檔（需 live 驗證後才可當親代）」且**預設不被自動挑中**。
- 提供一個**單一旗標** `groupsAllowPartnerParent`（localStorage，預設 `false`）。Phase 5 live 驗證為「可以」後翻 `true`，partner 成員才進入自動挑選池、手選時才不再警告。翻旗標**不需改任何後端**（見 §5）。

這樣不論 live 結果為何，現在就能 ship 群組 + own 自動配；partner 當親代只是一個已布線、待驗證即啟用的開關。

---

## 2. 資料模型

### 2.1 localStorage 形狀

Key：`'flypetGroups_' + ip()`（每裝置一份，與 `flypetPresets_`/`autoBreed_` 同慣例，see `:1945,:1954`）。

```jsonc
// localStorage['flypetGroups_emulator-5554'] =
[
  {
    "id": "g1718900000_512",        // genGroupId(): 'g' + Date.now() + '_' + rand
    "name": "收藏1",                  // 使用者可改名；預設 收藏N
    "members": [
      { "src": "own", "id": 80231 },                 // 自己的飛寵：只存 pet.id
      { "src": "partner", "role_id": 99001,          // 搭檔飛寵：存夠重新解析的最少欄位
        "role_name": "阿明", "pet_id": 70044,
        "config_id": 1207, "display_name": "炎角龍" }
    ]
  }
]
```

設計準則（KISS / 對齊既有 preset 形狀）：
- **own 成員只存 `id`**。名稱/詞條/品質在 use-time 從 `allPets`（`fly_pet_list` 已載入）即時解析，避免快取腐爛。
- **partner 成員存可重解析的最少欄位**：`role_id` + `pet_id`（= `info.id`）為主鍵；`role_name`/`config_id`/`display_name` 為**離線顯示快取**（搭檔未載入時仍能畫出成員名而不必先抓全部搭檔）。
- 不存詞條陣列（partner 詞條會變、且體積大）；要顯示詞條時走 `fly_pet_partner(role_id)` 即時抓。

### 2.2 成員在 use-time 的解析

```
own 成員    -> allPets.find(p => p.id === m.id)               // fly_pet_list 載入的清單
partner 成員 -> 先查 partnerPetCache[m.role_id]（doLoadAllPartners 抓過就有），
              再 .find(p => p.id === m.pet_id)；查無則用成員自身的離線快取欄位畫卡 + 標 STALE。
```

新增一個輕量 module-level 快取 `var partnerPetCache = {}`（`role_id -> RolePetListBack pets[]`），在 `doLoadAllPartners` 迴圈成功取得每位搭檔飛寵時順手寫入（`:1900-1903` 已有 `pets`，加一行 `partnerPetCache[f.role_id] = pets;`）。群組挑選器需要時可單獨 `fly_pet_partner(role_id)` 補抓。

### 2.3 STALE 標記

成員「失聯」判定（resolve 失敗）：
- own：`allPets` 裡找不到該 `id`（已分解 / 已上陣移除 / 換裝置）。
- partner：該 `role_id` 已不在 `role_list`（搭檔被踢出，遊戲上限 30），或 `partnerPetCache[role_id]` 裡找不到 `pet_id`（搭檔下架/分解了那隻）。

UI：STALE 成員在群組管理面板灰顯 + 「⚠ 失聯」徽章 + 「移除」鈕；隨機/填入挑選**自動略過** STALE 成員（與 `find_pair` 略過 cooldown/locked 同精神，`:721-733`）。不自動刪除（避免裝置沒載入就誤刪）。

---

## 3. `makeDeviceStore(prefix)` 重構（折疊三份 localStorage 樣板）

現況三份 per-device store 各自有重複的 key/load/save 樣板：

| store | key fn | load | save | 位置 |
|---|---|---|---|---|
| autoBreed | `abStorageKey` | `loadAbConfig` | `saveAbConfig` | `:1945-1949` |
| presets | `presetStorageKey` | `loadPresets` | `savePresets` | `:1954-1960` |
| **groups（新）** | （新） | （新） | （新） | 本功能 |

為了讓 groups 成為**第三個 consumer 而非第三份 copy**，在 `<script>` 早段（`ip()` 定義之後，`:713` 附近）新增工廠：

```js
// per-device localStorage helper: 折疊 key/load/save 樣板。
// fallback: 解析失敗或型別不符時回 fallback() 的新實例（避免共用同一參考）。
function makeDeviceStore(prefix, fallback) {
  fallback = fallback || function () { return {}; };
  function key() { return prefix + ip(); }
  function load() {
    try {
      var v = JSON.parse(localStorage.getItem(key()) || 'null');
      return (v === null || typeof v !== typeof fallback()) ? fallback() : v;
    } catch (e) { return fallback(); }
  }
  function save(v) { localStorage.setItem(key(), JSON.stringify(v)); }
  return { key: key, load: load, save: save };
}
```

接線（三者收斂；行為等價，型別防呆比現況更嚴）：

```js
// 取代 abStorageKey/loadAbConfig/saveAbConfig (:1945-1949)
var _abStore = makeDeviceStore('autoBreed_', function () { return {}; });
function loadAbConfig() { abConfig = _abStore.load(); }
function saveAbConfig() { _abStore.save(abConfig); }

// 取代 presetStorageKey/loadPresets/savePresets (:1954-1960)
var _presetStore = makeDeviceStore('flypetPresets_', function () { return []; });
function loadPresets() { abPresets = _presetStore.load(); if (!Array.isArray(abPresets)) abPresets = []; }
function savePresets() { _presetStore.save(abPresets); }

// 新：groups（第三個 consumer）
var flypetGroups = [];
var _groupStore = makeDeviceStore('flypetGroups_', function () { return []; });
function loadGroups() { flypetGroups = _groupStore.load(); if (!Array.isArray(flypetGroups)) flypetGroups = []; }
function saveGroups() { _groupStore.save(flypetGroups); }
```

> 為了讓 §6 契約測試仍能 grep 到字面 key，保留 `'autoBreed_'` / `'flypetPresets_'` / `'flypetGroups_'` 字串原樣傳入工廠（既有測試 `test_fly_pet_gallery.py` 沒測這些 key，但本功能測試會 assert `'flypetGroups_'` 字面存在）。
> `abStorageKey()`/`presetStorageKey()` 若沒有外部 caller 可直接刪；grep 確認僅自身使用後移除（保守作法：保留為 `_abStore.key` 的 thin alias 以防遺漏）。

---

## 4. UI 設計（建立在 Phase 2 元件庫上）

可用 lib 原語（`static/lib/app.js` 匯出於 `:692-715`，CSS 於 `static/lib/components.css`）：
`toast` (`app.js:142`)、`openModal`/`closeModal`/`closeTopModal` (`:201/:218/:245`)、`confirmDialog` (`:257`)、`keyboardable`/`applyKeyboardable` (`:367/:388`)、
`.btn--primary/--secondary/--ghost/--danger` (`components.css:77-105`)、`.modal-overlay` (`:167`)、`.chip`/`.chip--button`/`.chip.is-on` (`:380-401`)、`.empty-state`（`.es-icon/.es-title/.es-hint`，`:592-613`）、`.toast--ok/err/info` (`:549-551`)。
fly_pet.html 既有的 `showModal/hideModal` 已 route 到 `openModal/closeModal`（`:726-746`），新 UI 沿用既有 confirm modal 或改用 `confirmDialog`（async）。

### 4.1 「＋加入收藏」affordance（卡片 + 詳情抽屜）

**卡片 foot**（現況 markup `templates/fly_pet.html:976-979`，只有「選取 pill」+「詳情 ›」）：
在 `.card-foot` 的「詳情 ›」**之前**插一顆小群組鈕（icon-only，省空間），點開一個小 popover/或直接 toast 提示走詳情抽屜加入。為避免卡片 foot 過擠，**主加入入口放在詳情抽屜**（見下），卡片 foot 只放一顆 `data-group-add="<id>"` 的「＋收藏」icon 鈕，點擊開「選擇要加入哪個收藏」的小選單（`<select>` + 確認，或直接列出 `＋收藏N` 快捷）。

```html
<!-- 插在 :978 「詳情 ›」按鈕之前 -->
<button type="button" class="detail-link" data-group-add="' + p.id + '"
        aria-label="把' + name + '加入手選收藏">＋收藏</button>
```

**詳情抽屜**（現況 `mb-acts2` markup `:1294-1297`，有「加入選取」+「分解此隻」）：
在 `mb-acts2` 下方新增一列「加入手選收藏」區塊——一個 `<select id="detailGroupSel">`（列出現有群組 + 「＋ 新收藏…」）+「加入」鈕。事件在 `dScrim` 既有 delegated handler（`:1456-1470`）裡加分支：`data-detail-group-add`。

### 4.2 收藏管理面板（collapsible，置於配種方案面板附近）

放在「交配繁殖」section 內、`ab-presets`（配種方案）面板**之後**（`templates/fly_pet.html:669-676` 之後），或獨立一個 collapsible section（與「搭檔飛寵」section `:682-696` 同結構）。建議**獨立 collapsible section**「手選飛寵收藏」，預設收合，標題明確區隔遊戲內收藏：

```html
<div class="section">
  <div class="collapse-hdr" role="button" tabindex="0" aria-expanded="false" aria-controls="groupsBody"
       onclick="toggleCollapse('groups')" onkeydown="collapseKeydown(event,'groups')">
    <span class="section-title" style="margin:0;">手選飛寵收藏
      <span style="font-weight:400;font-size:0.82em;color:var(--text2);">（命名群組，配種時可指定整組挑兩隻）</span>
    </span>
    <span class="arrow" id="groupsArrow">&#9654;</span>
  </div>
  <div class="collapse-body" id="groupsBody">
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <button class="btn btn--primary btn-sm" onclick="groupNew()">＋ 新增收藏</button>
    </div>
    <div id="groupList"></div>            <!-- renderGroupList(): 每組一張卡 -->
  </div>
</div>
```

每組卡片（`renderGroupList`）內容：
- 標題列：組名（可點「改名」`groupRename(id)`）+ 成員數 + 「刪除」（`confirmDialog`，danger）。
- 成員 chip 牆：own 用 `petIcon` + 名 + 詞條摘要；partner 加 🤝 前綴 + `role_name`；STALE 成員灰顯 + ⚠。每個 chip 帶「✕ 移除」（`data-group-rm-member`）。沿用 `.chip`（`components.css:380`）。
- 空組顯示 `.empty-state`（「這個收藏還沒有成員 — 到卡片或詳情按『＋收藏』加入」）。

### 4.3 繁殖表單整合（從收藏填入 / 隨機自動挑）

在「開始交配」區（`templates/fly_pet.html:649-660`，`#bBase`/`#bFlyA`/`#bFlyB` 那一排）下方新增一列：

```html
<div class="ab-row" style="margin-top:6px;">
  <label style="font-size:0.78em;color:var(--text2);">從收藏挑</label>
  <select id="breedGroupSel" style="font-size:0.82em;min-width:130px;">
    <!-- options 由 refreshBreedGroupSel() 從 flypetGroups 填 -->
  </select>
  <button class="btn btn--ghost btn-sm" data-group-fill="A">填入 A</button>
  <button class="btn btn--ghost btn-sm" data-group-fill="B">填入 B</button>
  <button class="btn btn--ghost btn-sm" data-group-pick="random">隨機自動挑 A/B</button>
</div>
```

精確接線：
- **填入 A / 填入 B**（`fillBreedFromGroup(slot)`）：取 `#breedGroupSel` 選中的群組，解析其**有效**成員（own，且非 cooldown/breeding/locked/STALE——重用 `find_pair` 的略過邏輯：locked `:742`、fight `:744`、cooldown `use_pet_list.state>0` `:721-725`、breeding `home_list.fly_a/b` `:727-733`；前端用 `allPets` 的 `p.lock/p.fight` + breed_info 快取近似），跳出一個小挑選 modal（列出有效成員）讓使用者**手選一隻**，選定後呼叫既有 `setBreedPet(slot, petId)`（`:1721`）。
- **隨機自動挑 A/B**（`pickRandomFromGroup()`）：從同一組的有效成員隨機**不重複**抽兩隻 → `setBreedPet('A', a)` + `setBreedPet('B', b)`；不足兩隻則 `toast(...,'err')`。partner 成員是否納入由 `groupsAllowPartnerParent` 旗標決定（§1.3）。
- 三顆鈕都用 delegated handler（一次綁定，data-attr 分派），與 gallery 既有 pattern 一致（`:1378`）。

> 「指定槽位1從收藏1挑兩隻」= 使用者載入繁殖池選好巢穴（`#bBase`），把 `#breedGroupSel` 切到「收藏1」，按「隨機自動挑 A/B」（或各按填入 A/B 手選），再按既有「交配」鈕（`doBreed` `:1838`）。完全沿用既有 `send_66_27` 流程，零後端改動。

### 4.4 a11y / 響應式

- 所有自訂可點元素（chip / 成員卡）用 `keyboardable`（`app.js:367`）或原生 `<button>`；collapsible header 沿用既有 `collapseKeydown`（`:1741`）。
- 群組挑選 modal 用 `openModal/closeModal`（焦點移入/trap/Esc/還原，`app.js:201-249`）。
- 刪除用 `confirmDialog({danger:true})`（`app.js:257`）而非 `window.confirm`（現況 `presetDelete` 還在用原生 confirm `:2058`，新碼一律走 lib）。
- `<select>`/`.chip` 沿用 `components.css` 既有 focus ring；成員 chip 牆 `flex-wrap` 響應式不溢出。

---

## 5. 後端：需要新路由嗎？

**不需要。純前端 localStorage，零新路由。** 理由：
- 群組是「使用者偏好/書籤」，不是遊戲狀態；與 `flypetPresets_`/`autoBreed_` 同性質（純 client localStorage，`:1945-1960`）。
- 成員解析全靠**既有**端點：own 走 `fly_pet_list`（`routes_fly_pet.py:56`，已在 `doLoad` 載入 `allPets`），partner 走 `fly_pet_partner`（`:422`，`doLoadAllPartners` 已用）。
- 挑選/隨機/過濾在前端做即可（資料量 < 300，無效能問題；過濾規則與 `find_pair` 等價但不需要伺服器權威性——實際 `send_66_27` 時伺服器仍會權威性拒絕無效親代，前端過濾只是體驗）。

**唯一可能需要新路由的情境（明確排除於本期）**：若 live 驗證得出「partner 當親代需要伺服器先驗證該 partner pet 當前可借用」，且我們想在送出前先問伺服器。即便如此，現有 `fly_pet_partner(role_id)` 回傳的 `state`（0=可用，`:471`）已足夠前端判斷，仍不需新路由。

> 若**真的**未來要加（不在本期 scope，僅備格式範例，鏡像 `routes_fly_pet.py` 風格）：
> ```python
> @bp.route("/api/fly_pet_group_validate/<ip>", methods=["POST"])
> @_fly_pet_auth
> def fly_pet_group_validate(ip):
>     import control_panel_app as _cpa
>     data = request.json or {}
>     # ...組成員 id 清單 -> 在遊戲端核對 own 仍存在 / partner state===0...
>     return _cpa._cdp_json_response(ip, js, await_promise=True)
> ```

---

## 6. TDD 測試計畫

新檔 `tests/test_fly_pet_groups.py`，string-grep 契約風格，**鏡像** `tests/test_fly_pet_gallery.py`（同 `_t()` 讀 template、同 assert 子字串）。先寫、應 FAIL（功能未實作），實作後轉 PASS。

```python
from pathlib import Path
TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"
def _t() -> str: return TEMPLATE.read_text(encoding="utf-8")
```

契約斷言（HTML 符號）：

| 測試 | assert | 守住 |
|---|---|---|
| `test_device_store_factory_present` | `"function makeDeviceStore"` in html | §3 重構落地 |
| `test_three_stores_use_factory` | `"makeDeviceStore('autoBreed_'"`、`"makeDeviceStore('flypetPresets_'"`、`"makeDeviceStore('flypetGroups_'"` | 三 consumer 共用工廠（非三份 copy） |
| `test_groups_storage_key` | `"flypetGroups_"` in html | per-device key |
| `test_group_load_save_present` | `"function loadGroups"`、`"function saveGroups"`、`"var flypetGroups"` | load/save API |
| `test_group_crud_present` | `"function groupNew"`、`"groupRename"`、`"data-group-rm-member"`、`"renderGroupList"` | 建立/改名/刪成員/渲染 |
| `test_group_add_affordance_present` | `"data-group-add"`、`"＋收藏"` | 卡片/詳情加入入口 |
| `test_group_panel_labeled_to_disambiguate` | `"手選飛寵收藏"` in html | 與遊戲內 `is_collected` 收藏區隔 |
| `test_breed_form_group_integration` | `'id="breedGroupSel"'`、`"data-group-fill"`、`"隨機"`、`"data-group-pick"` | 填入 A/B + 隨機挑 |
| `test_random_pick_calls_setBreedPet` | `"setBreedPet('A'"`、`"setBreedPet('B'"`（在 `pickRandomFromGroup` 區段內，用 index-window 取子字串確認） | 隨機挑接回既有 form-fill |
| `test_member_src_own_partner` | `"src:"`/`"'own'"`/`"'partner'"`、`"role_id"`、`"pet_id"` | 混合成員資料模型 |
| `test_stale_member_handling` | `"失聯"` 或 `"STALE"`/`isStaleMember`、且隨機挑略過 | STALE 略過 |
| `test_partner_parent_flag_safe_default` | `"groupsAllowPartnerParent"`、`"false"`（旗標預設關，partner 不自動入池） | §1.3 安全解讀 |
| `test_confirm_uses_lib_dialog` | `"confirmDialog("` 出現在 group 刪除路徑（非 `window.confirm`） | a11y 刪除 |
| `test_partner_cache_wired` | `"partnerPetCache"` in html | partner 成員 use-time 解析 |

回歸（MUST 仍在，沿用既有測試精神）：
- `test_existing_presets_untouched`：`"flypetPresets_"`、`"function loadPresets"`、`"function savePresets"` 仍在（重構後行為等價）。
- `test_setBreedPet_still_present`：`"function setBreedPet"`（群組挑選接回它，`:1721`）。

**後端測試**：本期不加路由 → 不需後端 stub 測試。若 §5 的 `fly_pet_group_validate` 真的加入，才補一個 monkeypatch `_cpa._cdp_json_response` 的契約測試（鏡像 `tests/test_fly_pet_partner_source.py`）。

執行（focused，遵守 repo 約定不用裸 pytest）：
```bash
python -m pytest tests/test_fly_pet_groups.py tests/test_fly_pet_gallery.py -q
python -m py_compile control_panel/routes_fly_pet.py   # 若有動後端才需要
```

---

## 7. Phase 4 建置任務拆解（依序，每步小且可驗證）

1. **TDD red**：寫 `tests/test_fly_pet_groups.py`（§6 全部斷言），跑一次確認 FAIL。
2. **重構 makeDeviceStore**：在 `:713` 後加工廠；把 autoBreed/presets 改用工廠（`:1945-1960`）；跑既有 `test_fly_pet_gallery.py` + 新測試的 `test_three_stores_use_factory` 應綠（其餘仍紅）。**此步零行為變更**，先 commit。
3. **資料層**：加 `flypetGroups`/`loadGroups`/`saveGroups`/`genGroupId` + `partnerPetCache`（在 `doLoadAllPartners` 迴圈寫入）+ `isStaleMember(m)` + `resolveMember(m)` 解析器 + `groupsAllowPartnerParent` 旗標（localStorage，預設 false）。
4. **管理面板**：加「手選飛寵收藏」collapsible section（§4.2 markup）+ `renderGroupList`/`groupNew`/`groupRename`/`groupDelete`/`groupRemoveMember`，事件 delegated + `keyboardable`/`confirmDialog`。在 `DOMContentLoaded`（`:2381`）init 時 `loadGroups()` + 渲染。
5. **加入入口**：卡片 foot 加 `data-group-add`（`:978` 前）；詳情抽屜 `mb-acts2` 下加群組 select + 「加入」（`:1297` 後 + `dScrim` handler `:1456` 加分支）。
6. **繁殖整合**：開始交配區加 `#breedGroupSel` + 三鈕（§4.3 markup）；`refreshBreedGroupSel`/`fillBreedFromGroup`/`pickRandomFromGroup`（接回 `setBreedPet`），有效成員過濾（重用 own `p.lock/p.fight` + 近似 cooldown）。`doLoad`/`loadBreed` 成功後刷新 select。
7. **green + 自查**：`test_fly_pet_groups.py` 全綠；`py_compile`（若動後端）；人工核對文案「手選飛寵收藏」不與遊戲內收藏混淆。
8. **live 驗證 partner-as-parent**（Phase 5，§1.3 步驟）：成功則把 `groupsAllowPartnerParent` 預設仍留 `false` 但提供 UI 開關說明；失敗則保留 partner 成員為「標記/候選」用途，UI 文案標清「搭檔飛寵目前無法直接當親代」。

---

## 8. 開放風險

1. **partner 當親代未證實**（§1.3）：最大不確定。已用安全解讀（own-only 自動配 + partner 旗標待驗證）隔離，不阻塞本期 ship。
2. **own pet id 跨 reload 是否穩定**：群組存 `id`，若遊戲對同一隻飛寵在不同 session 給不同 `id`，own 成員會誤判 STALE。`fly_pet_list` 的 `pet.id`（`routes_fly_pet.py:110`）觀察上是穩定主鍵（分解用同一 id `send_66_8`，`:310`），風險低；STALE 略過邏輯已是安全網。
3. **partner role 流動**（遊戲上限 30、會被頂替）：partner 成員易 STALE。離線快取欄位（`role_name`/`display_name`）讓 UI 仍可顯示，STALE 不自動刪。
4. **localStorage 跨裝置不同步**：與既有 preset/autoBreed 同限制（per-device，換瀏覽器/清快取就沒了）。可接受；若要持久化才需後端（明確排除於本期）。
5. **有效成員過濾與 `find_pair` 不完全等價**：前端用 `allPets` 近似 cooldown/breeding（伺服器才權威）。最終 `send_66_27` 仍可能被拒——靠既有 `doBreed` 的 toast 回饋兜底，不影響資料正確性。
