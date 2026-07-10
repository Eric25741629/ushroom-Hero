# Spec: 星據車位(奇星車場)搶佔面板 + 預約 sniper

日期: 2026-07-10  帳號範圍: 先小寶 `7fe98fc6`(web_h5),但**不硬編歧視**——凡 web_h5 且有 web_debug_port 的裝置都應可用(使用者明確說「5554也可以打」)。

## 背景(全部 live 驗證 2026-07-10,see memory reference_carpark_rob_battle_protocol)

星據車位 = **奇星車場**(跨界車位第一列,4 個槽位)。這是**佇列制佔領戰**,跟曜鑽掠奪(cmd 12849)是**兩套不同系統,本面板只做星據搶佔**。

### 核心規則
- 每個奇星車場有 4 槽。可 **搶佔(攻)** = `queue_type:1`,**駐守(守)** = `queue_type:2`。
- **只能搶外服持有的槽;本服(自己 server_id)持有的槽只能駐守,不能攻**(使用者確認:自己不能打自己)。
- 槽有**保護倒數**;`free_end_time > serverTime` = 保護中不可攻。保護結束才可搶。
- **休戰期 22:00–次日10:00 無法發起搶佔。**
- 搶佔成功消耗 1 飛車泊能(上限3、自動回);挑戰失敗不消耗;失敗後 30 分不能再打同一玩家。

### 協議(cmd = car_park module)
- `server_car_info` **12860** (0x323c) 空 c2s → s2c `space_list`(4× `p_server_car_space`):
  - `pos#1`(1..4), `owner_server_id#2`(0=空), `attack_queue#3`(repeated member), `defend_queue#4`(repeated), `is_free#5`, **`free_end_time#6`(保護結束 epoch 秒)**, `buff_layer#7`, `mount_id#8`。
  - 頂層另有 `defend_cd_end_time#4` / `attack_cd_end_time#5` = 我方個人 CD。
- `server_car_queue` **12868** `{pos#1}` → `{pos, attack_queue#2, defend_queue#3}`,member `p_server_car_queue_member`: `role_id#1, server_id#2, role_name#3, queue_index#4(順位), status#5, info_list#6(p_role_change: kv/ks 裝戰力屬性), figure#7`。用來顯示**對手配置/戰力**(讀 defend_queue 首位的 info_list.kv)。
- `server_car_join` **12861** (0x323d) `{pos#1(1..4), queue_type#2(1攻/2守)}` → s2c `{code#1, pos#2, queue_type#3, queue_index#4}`。**無 loadout 欄。**
- `server_car_leave` 12862 `{pos, queue_type}`;`server_car_queue_stick` 置頂;推播 `server_car_queue_update`/`server_car_change`/`server_car_battle_result`/`server_car_settle`。
- Schema 檔:`docs/protocol/CARPARK_PROTO_SCHEMA.json` (server_car_* @ ~1414-1795),`docs/protocol/TYPE_PROTO_SCHEMA.json` (p_server_car_space @5610, member @5567)。

### 我方 server_id / serverTime / 套裝
- 我方 server_id:小寶 = **1467**(每裝置不同,需動態取得——login s2c 或 role_login 的 server_id;或用 server_car_info 頂層/role 屬性推)。**不可硬編 1467。**
- serverTime:`TimeUtil.serverTime`(秒,整數,無 sub-second);module-only,`System.import('chunks:///_virtual/TimeUtil.ts').then(m=>m.default)`。offset `D = TU.serverTime - Date.now()/1000`,`fireAtLocalMs=(free_end+0.1-D)*1000`。
- 套裝:server_car_join 不帶 loadout,要**先切方案再 join**。方案切換 `0x032a` body `08 <scheme_id>`;方案名稱 WS 讀不到,需維護 id→名對照或用現有 OCR(`game_actions/skill_manager.switch_skill`)。**v1 可先不做選套裝(用當前裝備),UI 留位。**

### 讀取鐵律(使用者 STRICT)
**倒數計時會被遮擋,不得「沒看到就當沒有倒數」。一律從資料層(WS free_end_time)判定可攻,不看 P 盾視覺。** 面板可 WS free_end_time + 場景樹 label 雙確認(已驗證兩者吻合到 1s)。

## 需求(使用者原話)
1. 主頁看得到的按鈕(參考工具類顯示),開一個獨立面板。
2. 面板可看 **4 個槽位的倒數計時**。
3. 可選**第幾個**槽(pos 1..4)。
4. 可選**什麼套裝**(loadout;v1 可留位/當前裝備)。
5. 可看**對手是什麼樣的配置**(defend_queue 首位 戰力/屬性)。
6. **預約**:倒數 N 秒後保護結束,自動在 `free_end+0.1s`(伺服器時間)送出搶佔;**實時追蹤伺服器時間,絕不早於保護結束**(慢 0.1s)。sniper 頁內執行、自我校正(隨 12860 更新 free_end/owner,變本服自動中止)、攔 12861 回應。
7. 「等等開打的槽位(live sniper)」與「dashboard 面板」**分開實作**(使用者同意)。

## 架構(沿用既有 pattern,see memory + 三份 subagent 研究)
- **後端**:新 route 群組(自己的 blueprint 或掛既有 `bp`),走 `control_panel/shared/cdp.py` `_cdp_evaluate/_cdp_json_response`(玩家 live session,不另開登入)。façade 晚綁定 `import control_panel_app as _cpa`。`require_device_access(ip)`。**gate = web_h5 且有 web_debug_port**,不硬編單一 ip。
  - `GET /api/star_seize/state/<ip>` → 注入 JS 讀 server_car_info(12860)+ 我方 server_id + serverTime,回 4 槽 {pos,owner,free_end,serverTime,attackable(owner!=me && free_end<=serverTime && !休戰),defQ,mount_id}。
  - `GET /api/star_seize/opponent/<ip>?pos=N` → server_car_queue(12868) 首位 info_list 戰力/屬性。
  - `POST /api/star_seize/seize/<ip>` `{pos, queue_type}` → 立即送 server_car_join(12861),回 code。
  - `POST /api/star_seize/snipe/<ip>` `{pos, queue_type}` → 注入自我校正 sniper(見 scratchpad arm_sniper.py 的 JS:hook 12860 更新 free_end/owner、serverTime>=free_end 開火、變本服中止、攔 12861)。
  - `GET /api/star_seize/snipe_status/<ip>` → 讀 `window.__sn`(armed/fired/aborted/reply/剩餘秒)。
  - `POST /api/star_seize/snipe_cancel/<ip>` → clearInterval + 清 __sn。
- **前端**(`templates/dashboard.html`):裝置卡 action-bar(~3240-3302)注入按鈕(gate web_h5),`openStarSeize(ip)` → `UI.openModal('#starSeizeModal')`。body 加一個 `#starSeizeModal.modal-overlay`,內含:4 槽倒數(前端每秒本地遞減 + 定期 refetch state)、pos 選擇(`.tab-bar--segmented`)、queue_type(搶佔/駐守)、套裝選擇(v1 留位)、對手配置區、[立即搶佔]/[預約狙擊]/[取消預約] 按鈕(危險動作用 `UI.confirmDialog({danger:true})`)、狙擊狀態列。用 `UI.toast`。設計系統走 `_assets_head.html` + tokens/components.css。
- 已在 scratchpad 驗證可用的 JS:`arm_sniper.py`(sniper)、`rigor.py`(雙確認讀 4 槽)、`send_pos2.py`(即時送+攔回應)。搬進 route 的 module-level JS 常數(pos/qt 以已驗證 int 內插)。

## 不做(v1 YAGNI)
- 不做真正的方案 OCR 切換(UI 留位,v1 用當前裝備);不做多目標排程佇列;不做曜鑽掠奪;不動 bot 主迴圈。

## 測試
- 後端 route 單元測試(mock `_cdp_json_response`/`_cdp_evaluate`):gate 非 web_h5 → 403/400;pos∉1..4、queue_type∉{1,2} → 400;合法 → 呼叫 CDP 一次且 JS 含正確 pos/qt。
- 不測 live(由使用者首點驗證;sniper 已在小寶 live 驗證中)。

## 風險
- 真送=真參戰(小寶主帳號);confirmDialog + require_device_access 為閘。
- 無 hot-reload:route 改動需重啟 `new_main_v2.py`。
- sniper 需瀏覽器保持開著(玩家 manual-hold session)。
