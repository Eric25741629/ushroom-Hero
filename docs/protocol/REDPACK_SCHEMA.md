# 紅包 (Red Envelope) 協議 (2026-05-20)

實驗環境：emulator-5554，bot launched via dashboard `web_launch`，CDP attach via 9230，
Cocos Creator 3.6.3 + protobuf-over-WebSocket。Schema 從 `docs/game_client_sources/`
的 game bundle 解出 + 真機 round-trip 驗證。

## Cmd Family

從 `index.966f5.js` 的 cmd 表抽出（搜 `"red.red_"`）：

| Hex (dec) | Name | 方向 | 用途 |
|-----------|------|------|------|
| `0x2601` (9729) | `red.red_all_list_c2s/s2c` | 雙 | 完整紅包列表（含已領） |
| `0x2602` (9730) | `red.red_send_c2s/s2c` | 雙 | 發紅包 |
| **`0x2603`** (9731) | **`red.red_grab_c2s/s2c`** | **雙** | **領取紅包** |
| `0x2604` (9732) | `red.red_pop_c2s/s2c` | 雙 | pop（疑為「開詳情」） |
| **`0x2605`** (9733) | **`red.red_brief_list_c2s/s2c`** | **雙** | **簡列表（detector 用）** |
| `0x0201` (513) | `error.error_info_s2c` | s2c | **通用錯誤通道（紅包失敗從這裡回）** |

## Request schemas

### `0x2605` brief list — request

```proto
// Empty body. Server returns 0x2605 response with brief list.
message RedBriefListReq {}
```

JS 源（line 4389）：
```js
e.send_red_brief_list_c2s = function() {
  netManager.send("red.red_brief_list_c2s", {})
}
```

### `0x2603` grab — request

```proto
message RedGrabReq {
  int64 id   = 1;  // bag_id (from brief list field 1)
  int32 type = 2;  // type (from brief list field 3; observed value: 2)
}
```

JS 源（line 4389）：
```js
e.send_red_grab_c2s = function(e, t) {
  netManager.send("red.red_grab_c2s", {id: e, type: t})
}
```

Wire example: `08 a3 c1 80 80 98 b0 14 10 02` = `id=89616640123043, type=2`

## Response schemas

### `0x2605` brief list — response (verified)

```proto
message RedBriefListResp {
  repeated RedBagEntry list = 2;
}

message RedBagEntry {
  int64 bag_id      = 1;  // 唯一紅包 ID (e.g. 89616640123043)
  int64 field_2     = 2;  // **未確認語意**（可能是 cfg_id 或金額；觀察值 100k~230k）
  int32 type        = 3;  // 觀察值一律 = 2，傳回 grab request
  int64 sender_id   = 4;  // 發送者 player ID
  string sender_name = 5; // 發送者顯示名（UTF-8）
  int32 unix_ts     = 6;  // 發送時間 (unix seconds)
}
```

實測 5 筆樣本（5554, 2026-05-20）：

| bag_id | sender_name | field_2 | type | unix_ts (interpreted) |
|--------|-------------|---------|------|----------------------|
| 89616640123043 | 全家禮服店 | 110712 | 2 | 2026-05-20 23:44:23 |
| 89616640123047 | 全家禮服店 | 100410 | 2 | 2026-05-20 23:57:03 |
| 89616640123048 | 下不維力炸醬麵 | 230007 | 2 | 2026-05-21 00:40:03 |
| 89616640123033 | 月光丨香香 | 150491 | 2 | 2026-05-20 11:47:47 |
| 89616640123046 | 全家禮服店 | 120162 | 2 | 2026-05-20 23:57:03 |

### `0x2603` grab — response (未 capture 過 success body)

成功 case 還沒抓到過（5554 上的 5 筆都已經領完）。預期格式應該類似：
```proto
message RedGrabResp {
  int64 bag_id   = 1;  // echo
  int32 amount   = 2;  // claimed gold/silver amount  -- 待驗證
  // 其它欄位可能有 sender_name 等
}
```

當下次有實際可領紅包時，`utils.redpack_detector.claim_redpack` 會把 raw bytes
存進 `ClaimResult.response_body`，可以直接從那邊看，再來 calibrate schema。

### `0x0201` error — response (verified)

```proto
message ErrorInfo {
  int32 error_code = 1;  // 觀察到的值: 2 (已領完/過期)
  // 可能還有 ext_info / message bytes，未在 grab 失敗 case 觀察到
}
```

實測：對歷史紅包送 0x2603 grab，server 回 `0x0201 body=08 02`（field 1 = 2）。

**已知 error_code**：

| code | 推測語意 | 觸發場景 |
|------|----------|---------|
| 2 | 已領完 / 過期 / 紅包不存在 | 對 5 筆歷史紅包送 grab 都回這個 |
| other | 未觀察過 | (待補) |

JS 端有 `configErrorInfo.getDataByKey(code)` lookup（line 21317 用例：error 147），
完整 error message 表在 game 的 config 資料裡。

## Cocos paths（UI 偵測）

### 主頁的 RedPoint 指示（用於 cheap gate）

```
/UIRoot/NormalView/MainView/subRoots/btnChatRoot/imgChatIcon/RedPoint
├── point       ← active=true 時：有未讀
├── point1      ← active=true 時：有未讀（變體）
└── txtNum      ← active=true 時：顯示未讀數字
```

任何子節點 active=true → 「有未讀」（含紅包 OR 一般聊天訊息）。

### 聊天面板內的紅包 tab（精準判斷紅包專屬）

```
/UIRoot/NormalView/ChatView/container/tab/redBagBtn          # 紅包 tab 按鈕
/UIRoot/NormalView/ChatView/container/tab/redBagBtn/Label    # 文字 "紅包"
/UIRoot/NormalView/ChatView/container/tab/redBagBtn/RedPoint # 紅包專屬紅點
```

要打開聊天 panel 才能讀到（cost ~1.5 秒）。日常檢測不建議走這條，用 API + main RedPoint 組合。

## Production API（`utils/redpack_detector.py`）

| Function | 用途 |
|----------|------|
| `main_redpoint_state(page) -> dict \| None` | dump RedPoint 容器狀態（forensics 用） |
| `main_redpoint_has_unread(page) -> bool` | cheap gate (~ms)：是否有任何未讀 |
| `fetch_redbag_list(page, timeout) -> List[RedBagEntry]` | 送 0x2605 + parse |
| `has_claimable_redpack(page) -> bool` | gate → fetch 短路：是否有可領 |
| `claim_redpack(page, bag_id, type_, timeout) -> ClaimResult` | 送 0x2603 + 收 0x2603/0x0201 |
| `claim_all_pending(page) -> (count, results)` | gate → fetch → 全部 grab |

### `ClaimResult` 欄位

```python
@dataclass
class ClaimResult:
    bag_id: int
    success: bool                # True iff response_cmd == 0x2603
    response_cmd: int = 0        # 0x2603 (success) or 0x0201 (error)
    response_body: bytes = b""   # raw bytes (forensics)
    response_fields: dict        # parsed top-level proto fields
    error: Optional[str] = None  # 'install failed' / 'timeout' / 'server error code=N'
    error_code: Optional[int]    # 從 0x0201 field 1 解出
    amount: Optional[int]        # 待 schema calibration（目前永遠 None）
```

## 接入 new_main_v2

`_run_redpack_check_if_due(d, ip)` 在 `_run_daily_tasks` 開頭（Task 0）觸發，gated by：

1. `backend == "web_h5"`
2. `getattr(d, "_page", None) is not None`

→ 所有 HTML / web_h5 裝置都會跑紅包自動領取；ADB-backend 裝置完全 no-op。

每次任務循環一次 log：
```
[emulator-5554] 紅包檢查: gate off (無未讀)             # 平時
[emulator-5554] 紅包檢查: claimed=2/5 | OK#A, ERR2#B, OK#C, ...  # 有紅包時
```

## Tests

| 檔案 | 模式 | Count |
|------|------|-------|
| `tests/test_redpack_detector.py` | mock (CI-safe) | 29 |
| `tests/integration/test_redpack_detector_live.py` | real device 5554 | 7 |

真機 tests 涵蓋：
- RedPoint 節點存在
- fetch_redbag_list 對真實 server roundtrip + body parse
- claim_redpack 對歷史紅包 → 預期 error_code 回應
- 整個 pipeline 不會回 "no response"（schema 對證）

## 重要：必須用 `netManager.send`，不能用 raw `sock.sendMessage`

2026-05-20 修正：grab `0x2603` 不能像 `0x2605` empty body 那樣用低階送，必須走
`window.netManager.send("red.red_grab_c2s", {id, type})`。

低階 `sock.sendMessage(0x2603, raw_bytes)` 雖然 wire format 看起來正確
(`08 <varint id> 10 <varint type>`)，server 一律回 `0x0201 error_code=2`。
原因應該是 `netManager.send` 會經過 protobuf .proto schema 序列化層，加上 server
需要的封包元資料 (timing token / signature / wrapper)；raw bytes 跳過這層就被
server 視為 malformed。

✅ 對：
```js
netManager.send("red.red_grab_c2s", {id: 89616640123043, type: 1})
```

❌ 錯：
```js
sock.sendMessage(0x2603, Uint8Array.from([0x08, 0xa3, ..., 0x10, 0x01]))
// → server 一律回 0x0201 error_code=2
```

對 empty-body cmd (例如 `red.red_brief_list_c2s` / `0x2605`)，raw `sock.sendMessage`
意外能用 — 大概因為沒 fields 可驗證。但對需要 body 的 cmd 必須走高階。

**implementation note**: `utils/redpack_detector._SEND_2603_JS` 已改用 `netManager.send`。
`_build_grab_body` 函式還留著但已不在 `claim_redpack` 中使用，只作為 wire format
文件參考。

## 還待驗證的欄位

清單追在這裡，下次紅包出現時 calibrate：

1. **`field_2` of `RedBagEntry`**：值 100k-230k，可能是 cfg_id（指向 `configRed_packet`）或金額
2. **`0x2603` success body 結構**：哪個 field 是 amount、是否有 sender info
3. **`type` 的其它值**：目前只見過 2，活動/系統紅包可能是其它值
4. **`error_code` 對應表**：目前只見過 2

驗證方法（當紅包出現時自動會做）：
- `claim_redpack` 把 `response_body` 存下來
- 對著 game UI 顯示的「+N 金幣」做 cross-reference 找出 amount 對應哪個 field

## Bundled tools

- `tools/decode_0x2605.py` — 送 0x2605 + walk protobuf + 漂亮 print
- `tools/test_claim_one.py` — 對 list 第一筆送 grab，驗 schema
- `tools/test_claim_observe_all.py` — 送 grab + capture 所有後續 cmd（找 response cmd）
- `tools/demo_redpack_detector.py` — 一鍵展示 detector 三層 API 在真機輸出
- `tools/find_redpack.py` — scene tree 搜紅包關鍵字
- `tools/open_chat_and_probe.py` — 打開聊天 + 掃紅點/紅包節點
- `tools/click_redbag_tab.py` — 點紅包 tab + 抓 WS diff
- `tools/probe_chat_root_redpoint.py` — 檢查主頁 btnChatRoot 的 RedPoint 結構
