# 手機離線純 WS 掛機備援（offline_fallback）設計

日期：2026-06-12
對象裝置：`adb-fc65396d-4LPqmI._adb-tls-connect._tcp`（手機fc，backend=adb，ws_token+bootstrap_token 已啟用）
狀態：設計已過使用者口頭核可（方案 B 混合備援）

## 1. 需求

手機fc 默認開啟掛機。手機在線（ADB 可達）→ 照跑現有 ADB pipeline（WS-first 階段照常先跑）；
手機不在線（ADB 不可達）→ 改用既有純 WS 流程（`game_actions/ws_phase.py` → `ws_token/runner.py`）
持續掛機領獎，手機回來自動恢復完整流程。Dashboard 提供 per-device 開關。

## 2. 現況（已存在的零件，勿重造）

| 零件 | 位置 | 現況 |
|------|------|------|
| WS-first 階段 | `game_actions/ws_phase.py` | wake 時先跑純 WS 任務（idle_reward/lamp/mining/steward/couple...），失敗自然降級 |
| 中途斷線降級 | `new_main_v2.py:407-420` | `PhoneUnreachableError` → 本輪只保留 WS 結果、照常對齊睡眠、thread 不死（2026-06-11 已做） |
| token 快取/重撈 | `ws_token/bootstrap.py` | capture 存在就不碰 ADB；login 失敗才 force 重撈（需 ADB） |
| 純 WS 裝置注入 | `runtime_services/device_scan_service.py:149-169,220-222` | `use_ws_runner` 裝置不靠 ADB 掃描、由 config 注入 |

**真正缺口只有三塊：**
1. 手機離線時 ADB 掃描掃不到 → thread 不會 spawn → WS 完全不跑。
2. thread init 階段 u2 連不上 → `handle_connect_failure` + `set_offline` + return（`new_main_v2.py:193-196`），不會進主迴圈。
3. Dashboard 沒有此開關。

## 3. 設計

### 3.1 Config

- 新 key：`ws_token.offline_fallback`（bool，預設 `false`）。
- `config_manager.py` `DEFAULT_DEVICE_CONFIG` 的 ws_token 預設加 `offline_fallback: false`。
- `bot_config.json` 手機fc 設 `true`。

### 3.2 掃描注入（device_scan_service）

新增 helper `get_ws_fallback_devices(logger_obj)`（鏡像 `get_ws_runner_devices`）：
條件 = `backend == "adb"` 且 `ws_token.enabled` 且 `ws_token.offline_fallback` 且 `enabled != false`。
掃描迴圈在注入 ws_runner 裝置處同樣注入這批 serial（不在 `current_devices` 才補），
master/worker 都注入；worker 的 emulator-* strip 不影響（手機 serial 非 emulator-*）。

### 3.3 Init 失敗改走 WS 等待迴圈（new_main_v2）

`main()` 的 init while-loop 連線失敗 except 分支（`new_main_v2.py:181-196`）：

```
except Exception as e:
    if backend_kind == "adb" 且 offline_fallback 開啟:
        handle_connect_failure(...)  # 照記
        → 進入「WS 等待迴圈」：
           每輪: _run_ws_phase_for_wake(ip, device_logger)   # 既有，capture 在就能跑
                 run_sleep_cycle(...)                          # 既有對齊睡眠/pause/force-sleep
                 下一輪 continue 重試 initialize_runtime_device
    else: 原行為（set_offline + return）
```

實作上抽小函式 `_ws_fallback_wait_round(ip, device_logger) -> None`（跑一輪 WS + 睡眠），
init except 分支呼叫後 `continue` 回 init while-loop 重試連線。手機回來 → init 成功 →
`break` 進正常主迴圈，行為與今日完全相同。中途斷線已由 `PhoneUnreachableError` 分支涵蓋，不動。

狀態回報：等待迴圈中 `bot_state.update_state(ip, task="手機離線", step="WS 備援掛機中，等待手機回線")`，
避免 dashboard 誤判離線（注意 2026-06-11 的掉線判離線規則：此 thread 活著且有 update_state 即可見）。

### 3.4 Dashboard

- 裝置設定窗 WS 選項區新增 checkbox `chkWsOfflineFallback`：「手機離線時改跑純 WS（離線備援）」。
- 顯示條件：方案 = `adb+ws`（`isWsPlanSelected` 且 base backend 為 adb）。
- `openSettings` 載入 `_existingWsToken.offline_fallback`；`saveConfig` merge 進
  `payload.ws_token`（沿用既有 `_existingWsToken` spread，不洗掉其他欄位）。

### 3.5 安全護欄

- 手機 ADB 不可達 ≠ 沒人在玩（可能帶出門玩）。WS 登入會踢人。緩解：
  - 此風險 2026-06-11 中途斷線降級已存在（WS 階段本來就每輪先跑），非本案新增。
  - runner 回報 `kicked` 時 ws_phase 已記 log；備援輪固定走對齊睡眠（每小時 parity），最壞每輪踢一次。
  - 若日後要保護：該裝置補 `online_check_target_pid`，改走 `run_ws_device_cycle` 的既有在線保護（本案不做）。
- token 過期：login 失敗 → ws_phase force 重撈需 ADB → 失敗 → 回空集合，該輪純睡眠；手機回線後自癒。

### 3.6 測試

1. `tests/test_device_scan_ws_fallback.py`（或併入既有 scan 測試）：注入條件矩陣
   （offline_fallback on/off、enabled=false、backend=web_h5 不注入）。
2. `tests/test_wake_ws_fallback.py`：init 連線失敗 + 開關開 → 跑 WS 階段 + 睡眠 + 重試 init；
   開關關 → 原 set_offline 行為；手機回線 → 退出等待迴圈。mock `initialize_runtime_device` /
   `_run_ws_phase_for_wake` / `run_sleep_cycle`。
3. `tests/test_dashboard_template.py` 追加：checkbox 存在 + saveConfig round-trip 保留
   ws_token 其餘欄位。

### 3.7 降級保證

開關預設關 → 其他裝置零行為差異。等待迴圈任何例外只記 log、睡下一輪，不炸 thread。

## 4. 關聯事項

- `tasks/todo.md` 的「adb-fc65396d 每日 10:00 泊銀9/10 自動跨界停車」計畫假設
  「手機不在 ADB 上時裝置 wake loop 不會跑」——本案實作後該假設失效（thread 會 spawn 並每輪跑 WS），
  該計畫可改掛在 ws_phase/備援輪內（後續再議，本案不動）。
- 改 `new_main_v2.py` / `device_scan_service.py` 需重啟 master+worker 生效。
