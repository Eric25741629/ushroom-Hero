# 家族驚喜寶箱：CDP 現場觀察與 WS 待驗證欄位

更新日期：2026-08-05
現場裝置：`7fe98fc6`（小寶）
CDP：`http://127.0.0.1:9226`
遊戲頁面：`https://mushroomh5.acenetgame.com/`

## 本次已確認的 CDP 行為

本次只使用 CDP 截圖、Cocos 節點文字與 `page.mouse.click()`；**沒有同步擷取
WebSocket frame**，因此下列 UI 行為是實證，WS 欄位對照仍需下一輪抓包確認。

1. 活動開始時頂部顯示「新一輪寶箱已刷新」，同時可讀到：
   - `本輪剩餘時間：55秒`
   - `已搶寶箱：1/5`
2. 地圖上每個寶箱有獨立氣泡，例如 `0/6`；靠近後可看到 `1/6`。
3. 從角色所在地直接點較遠的寶箱，只會先讓角色移動並造成鏡頭捲動；計數當下仍是
   `1/5`，不代表已拾取。
4. 角色進入寶箱附近後，需要再點寶箱本體。成功時 Cocos 節點出現
   `已拾取該寶箱`，使用者也在現場確認已領到。
5. 第二次點擊後讀到的頂部狀態為 `已搶寶箱：3/5`。因本次未捕捉中間的 WS
   frame，不能據此斷言一次操作固定增加 2；也可能有並行更新或前一個狀態擷取落後。
6. 該輪結束後，寶箱活動介面消失；後續畫面已切換到「征戰熔岩巨獸」，無法追溯
   前一輪已完成的 WS frame。

## 與既有 guild schema 的高可信映射

專案現有 schema 與實作：

- `guild.guild_area_move`：`0x1D1A`（7450），角色在家族地圖移動。
- `guild.guild_treasure_info`：`0x1D23`（7459）。
- `guild.guild_treasure_open`：`0x1D24`（7460）。

依 `docs/protocol/GUILD_PROTO_SCHEMA.json`、`docs/protocol/TYPE_PROTO_SCHEMA.json` 與
`ws_token/guild.py`，目前最合理的 UI 對照如下：

| 畫面資訊 | 候選 WS 欄位 | 信心 | 備註 |
|---|---|---:|---|
| 本輪剩餘時間 | `guild_treasure_info_s2c.countdown#5` | 高 | 數值與倒數語意完全一致，仍待同 frame 比對 |
| 已搶寶箱 `x/5` | `guild_treasure_info_s2c.my_open#4` + 活動個人上限 | 高 | `my_open` 很可能是已拾取數；分母 5 可能來自活動 config，不一定在 wire |
| 寶箱氣泡 `x/6` | `p_guild_treasure_box.open_num#3 / open_limit#4` | 高 | 每個寶箱各自顯示，形狀與欄位完全吻合 |
| 寶箱在地圖的位置 | `p_guild_treasure_box.pos#2:p_pos` | 高 | 應用於角色尋路／靠近寶箱 |
| 寶箱身分 | `p_guild_treasure_box.n#1` | 高 | `guild_treasure_open_c2s.n#2` 應引用同一值 |
| 移動路徑 | `guild_area_move_c2s.pos_list` | 中高 | 靜態 bundle 已確認 send call；尚未與本次點擊同步抓 frame |
| 拾取請求 | `guild_treasure_open_c2s {round#1, n#2}` | 高 | schema 與現有 byte-locked 測試已確認；尚未證明本次 UI 點擊送出的實際 frame |

## 下一輪最小驗證程序

必須在活動開始前掛上 CDP WebSocket 監聽，不能等活動結束後補抓：

1. 先記錄 `0x1D23` 回應，保存原始 bytes 及解碼後的
   `round/cfg_id/my_open/countdown/box_list`。
2. 只點一次遠處寶箱，確認移動期間是否送出 `0x1D1A`，並保存 `pos_list`。
3. 角色靠近後、點寶箱前再次讀 `0x1D23`，確認氣泡 `open_num/open_limit`。
4. 點寶箱本體，抓同一時間窗的 `0x1D24` c2s/s2c，核對 `{round, n}` 與
   `{round, n, code}`。
5. 成功提示出現後再讀一次 `0x1D23`，比對 `my_open` 及該箱 `open_num` 的增量。
6. 每一步同時保存截圖時間戳，避免把其他玩家或伺服器推播造成的並行更新誤判成
   本機一次點擊的結果。

在完成上述同步抓包前，不應把 `1/5 -> 3/5` 寫成固定加值規則，也不應據此修改
`ws_token/guild.py` 的開箱迴圈。
