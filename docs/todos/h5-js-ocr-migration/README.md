# H5 JavaScript 判斷遷移任務索引

目標：`web_h5` 的正常流程全部使用 WebSocket、runtime 或 Cocos，只有 `adb` 保留 OCR。完整盤點與遷移 TODO 見 [11-all-ocr-cocos-migration.md](11-all-ocr-cocos-migration.md)。

## 共通執行順序

1. 已有純 WS 任務：純 WS 成功後直接跳過 UI 任務。
2. 純 WS 未覆蓋或失敗：`web_h5` 透過 Playwright `page.evaluate()` 讀取 Cocos/runtime 狀態並操作。
3. JavaScript/Cocos 找不到節點、頁面尚未載入、版本更新或執行例外：`web_h5` 記錄原因後有限重試、回安全頁或重新排程；不得走 OCR。
4. `adb`：維持現有 OCR、影像與座標路徑。

禁止把「取到 `_page`」視為 JavaScript 成功。只有讀到可驗證狀態或完成操作才可略過 OCR。

## 共用交付要求

- 抽出共用 helper，優先重用 `utils/cocos_view.py`、`utils/cocos_navigator.py`、`utils/page_detector.py`。
- 每個判斷回傳來源：`ws`、`cocos`、`runtime`、`adb_ocr` 或 `unavailable`。`ocr_fallback` 僅可存在於 ADB 分支的語意，不可出現在 web_h5。
- JavaScript 例外、節點不存在與 timeout 都必須可觀測，不能靜默宣稱成功。
- web_h5 失敗必須使用 bounded retry/safe stop/requeue；ADB 才可呼叫原有 OCR 函式。
- 測試至少覆蓋：H5 Cocos 成功、H5 Cocos 失敗且不呼叫 OCR、ADB 直接 OCR。
- action trace 應能比較遷移前後 OCR 次數。

## 建議分派順序

| 優先級 | 任務 | 目前判斷 |
|---|---|---|
| P0 | [挖礦](01-mining.md) | 道具數量已有 WS，優先移除 overlay 與週期 OCR |
| P0 | [農場](02-farm.md) | 已有大量 H5 helper，適合快速收斂殘餘 OCR |
| P0 | [競技場](04-arena.md) | 動畫模式仍頻繁 OCR，可改讀節點與戰鬥 runtime |
| P0 | [家族任務](05-family.md) | 舊流程大量 OCR/座標，收益高但需 live 探查 |
| P1 | [啟動與頁面恢復](06-startup-page-recovery.md) | 已有 PageDetector，需補齊 fingerprint |
| P1 | [雲端戰鬥](07-cloud-battle.md) | 已能直開 view，其餘 UI 流程仍偏 OCR |
| P1 | [開神燈](10-lamp.md) | 已有 Cocos/封包路徑，只改殘餘屬性 OCR |
| P2 | [萬神試煉](03-wanshen.md) | 戰鬥已零 OCR，只剩周邊流程 |
| P2 | [好友禮物](08-friend-gifts.md) | 伴侶禮物已有 WS，補 UI fallback 即可 |
| P2 | [商店](09-shop.md) | 管家代購已有 WS，補 UI fallback 即可 |

## 不建議多人同時修改的共用檔案

- `utils/page_detector.py`
- `utils/cocos_navigator.py`
- `utils/cocos_view.py`
- `game_actions/ws_phase.py`
- `game_actions/daily_pipeline.py`

先指定一人負責共用 helper，其他任務只在各自模組接入，可降低衝突。
