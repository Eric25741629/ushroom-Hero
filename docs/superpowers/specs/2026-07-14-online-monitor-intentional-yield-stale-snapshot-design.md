# Online Monitor 刻意讓路舊快照設計

日期：2026-07-14
範圍：修復 carpark 排程在 09:59 前使 online monitor 因無閒置 detector 主動斷線，進而令 `human_played` 手機帳號無限等待、錯過搶位開窗的結構性問題。

## 1. 問題與根因

`ws_token/online_monitor.py` 會在目前 detector 即將喚醒、且找不到其他安全 detector 時走 `desired is None` 分支，記錄 `no idle detector; disconnecting ...` 後主動關閉連線。最後一份好友在線快照仍保留，但 `account_online()` 只接受 60 秒內的快照；超時後所有帳號都變成 `None`。

`game_actions/ws_phase.py::_wait_until_human_offline()` 對 `human_played=True` 的帳號把 `None` 視為可能在線並無限等待。carpark 又會把所有裝置的 `next_wake_at` 集中到 09:59，因此排程先把 monitor 擠下線，monitor 的舊快照隨後又擋住手機登入，形成固定的循環依賴。

本修復不改動 2026-06-25 的一般保護規則。只有能證明 monitor 是因 `no idle detector` 主動讓路時，才允許手機閘門有限度使用讓路前最後一份「離線」快照。

## 2. 採用方案

### 2.1 讓路標記

`OnlineMonitor` 單例新增只存在記憶體內的讓路標記，至少保存：

- 讓路原因：固定為 `no_idle_detector`；
- 讓路前最後一份快照的 `timestamp`。

標記不落地檔案，因 monitor 與 `ws_phase` 位於同一進程。快照時限必須使用標記中鎖定的 timestamp 計算，不重新取讀取當下的 `snapshot.timestamp` 作為時間錨點。查詢時仍須確認目前保留的 snapshot 與標記 timestamp 相符，避免未來 snapshot 生命週期調整後誤用另一份快照。

只有 `_loop()` 的 `desired is None` 且當下確實持有 live detector/client、即將記錄 `no idle detector; disconnecting` 的分支可以建立標記。

以下路徑不得建立同一標記：

- detector 被 `SCHEDULER` 搶佔的 `_preempted` 路徑；
- `poll_friends` 失敗；
- connect 失敗；
- registry acquire 衝突；
- monitor 停止或 thread 發生例外。

`_preempted` 雖然也是主動讓出 detector，但代表該帳號正在被登入使用、狀態正在轉換，不能視為可重用舊 presence 的安全窗口。

### 2.2 清除規則

成功建立新的 detector 連線後，立即清除讓路標記。其他非 `no_idle_detector` 的斷線/失敗路徑也清除標記，確保舊的安全窗口不會跨越新的連線生命週期或被錯誤沿用。

重連清除後，閘門立刻恢復現有 60 秒新鮮度規則；即使原讓路快照仍在 10 分鐘內，也不得再由例外路徑採信。

### 2.3 閘門專用 presence 查詢

保留一般 `account_online()` 的既有 60 秒語意，避免 dashboard、online check 或其他呼叫者意外接受舊資料。新增或擴充一個只供喚醒閘門使用的窄介面，其判定順序如下：

1. 若目前快照在 60 秒內，照現行規則回傳 `True`、`False` 或 `None`。
2. 若快照已過 60 秒，只在下列條件全部成立時回傳 `False`：
   - monitor 目前沒有 active detector；
   - 存在 `no_idle_detector` 讓路標記；
   - 標記的 snapshot timestamp 與目前保留快照相符；
   - 目前時間減去標記 timestamp 不超過 600 秒；
   - 目標 roleId 存在於該快照，且快照明確顯示 `online=False`。
3. 任一條件不成立皆回傳 `None`。特別是舊快照顯示 `online=True` 時不可轉成 `False`，仍維持保守等待。

`ws_phase._account_online()` 改用這個閘門專用查詢；`_wait_until_human_offline()` 的迴圈與 `human_played` 無限等待規則保持不變。

## 3. 資料流

```text
online monitor 最後一次 poll 成功
  → Snapshot(timestamp=T, 手機=offline)
  → carpark 將所有 detector 候選拉到即將喚醒
  → desired is None 且目前有 live client
  → 記錄 intentional-yield(snapshot_timestamp=T)
  → 關閉 detector
  → 手機 09:59 進入 human-offline gate
  → 新鮮查詢已超過 60 秒
  → gate-only 查詢驗證 yield 標記、timestamp、600 秒上限及 offline entry
  → 回傳 False，立即放行搶位
```

若 monitor 在此期間成功重連，流程改為：

```text
connect 成功 → 清除 intentional-yield → gate-only 查詢不再接受舊快照
             → 僅按正常 60 秒規則判定
```

## 4. 安全邊界

- 例外只放寬「刻意無 detector 讓路 + 舊快照明確離線」的交集，不把一般 stale snapshot 視為可信。
- 10 分鐘從讓路前快照 timestamp 起算，不從閘門第一次查詢或斷線後任意時間起算。
- stale-online、roleId 不在好友清單、無 snapshot、timestamp 不匹配、超過 600 秒，全部維持 `None`。
- preempt、poll failure、connect failure 不得共用 no-idle 標記。
- 風險維持為已接受的產品取捨：真人若在最後離線快照後、10 分鐘窗口內剛好拿起手機，排程登入可能踢掉真人一次；換取固定 09:59 搶位不再被 monitor 自己阻塞。

## 5. 測試設計

在 `tests/test_ws_human_offline_gate.py` 與必要的 `tests/test_online_monitor.py` 補回歸測試：

1. 一般新鮮快照仍依 60 秒規則回傳 online/offline。
2. `no_idle_detector` 標記存在、timestamp 相符、快照在 10 分鐘內且目標明確 offline → 閘門查詢回 `False`。
3. 同樣條件但超過 10 分鐘 → `None`。
4. 讓路快照顯示 online → `None`。
5. 無標記，或斷線原因是 preempt/poll/connect failure → `None`。
6. 標記 timestamp 與目前 snapshot 不相符 → `None`。
7. 設定讓路標記後成功重連 → 標記清除；超過 60 秒的舊快照重新回到 `None`。
8. `_wait_until_human_offline()` 收到符合條件的 `False` 時不 sleep、立即放行。

TDD 順序：先加入能重現 7/13 情境且在現況失敗的測試，確認 RED；再做最小實作，確認目標測試 GREEN；最後執行相關 monitor/gate 測試與指定檔案的 `py_compile`。

## 6. 非目標

- 不改 carpark 的 09:59 排程或 `_about_to_wake` 門檻。
- 不讓 monitor 在 detector 即將喚醒時繼續佔用帳號。
- 不修改 `human_played` 在一般 unknown 狀態下無限等待的政策。
- 不把 10 分鐘舊快照規則套用到 dashboard 或其他 presence 消費者。
- 不持久化讓路標記，也不跨進程或跨重啟恢復窗口。
