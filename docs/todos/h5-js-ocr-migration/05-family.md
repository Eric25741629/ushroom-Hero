# TODO：家族任務 H5 JavaScript 判斷

## 現況

- `family.py` 是舊式 OCR、模板匹配與固定座標混合流程。
- `battle/special.py::fight_snow_country` 以 OCR 輪詢雪國危機各階段。
- `ws_token/guild.py` 已能完成部分家族任務；WS 成功會跳過 `家族任務`。

## 待辦

- [ ] 先列出 WS guild 已覆蓋的捐獻、獎勵與任務，避免 H5 重做或重複消耗。
- [ ] live 探查家族大廳、捐獻、家族寶箱、家族活動與雪國危機相關 view/node/component。
- [ ] 將家族導航改成 `uiMgr.openView` 或 Cocos tab/node 路徑並驗證 view active。
- [ ] 將捐獻次數、可領寶箱、一鍵領取狀態改讀 label、badge 或 component data。
- [ ] 為雪國危機建立獨立 H5 driver，讀取入口、組隊、速戰、挑戰結果及獎勵 view。
- [ ] 不在同一函式混跑 H5 與 OCR 步驟；H5 driver 整段失敗才交回舊函式。
- [ ] ADB 保留 `family.py` 與 `fight_snow_country` 原路徑。

## 驗收

- [ ] WS 已完成的家族工作不再開 UI。
- [ ] H5 家族正常流程與雪國危機正常流程零 OCR。
- [ ] 已捐滿、無寶箱、活動未開、組隊失敗與版本節點變動都有 fallback。
