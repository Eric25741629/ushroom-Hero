---
name: family-quiz-protocol
description: Inspect and decode the Mushroom game's family/guild quiz and family chat protocols through CDP, including 家族趣味競賽, 家族趣味競答, 家族競答, 家族智多星, quiz questions, answers, rankings, dice results, cmd 1537/1538/1542/7446/7447/7457/7458, configQuiz, or requests to analyze the latest 5554/5560 family chat packets. Use whenever these Chinese terms or protocol IDs are mentioned.
---

# 家族趣味競答協議

先完整閱讀 [`docs/protocol/FAMILY_QUIZ_PROTOCOL.md`](../../../docs/protocol/FAMILY_QUIZ_PROTOCOL.md)，再進行查詢、分析或實作。該文件是協議、欄位、CDP 流程與實測結果的單一真相來源。

## 操作規則

1. 先從 `bot_config.json` 讀取裝置的 `web_debug_port`，不要硬編碼 port。
2. 優先使用 `tools/rawcdp.py` 與客戶端 `netManager` 事件做唯讀取證。
3. 事後分析家族聊天時查 `channel=3` 的 cmd `1538`。
4. 要取得確切題目時，必須在活動期間攔截 cmd `7446`，並用 `configQuiz` 解析 `question_id`。
5. 結算時解析 `p_link.type=7` 的 `string_list[0]` JSON。
6. 分析完移除臨時 listener 或還原封包 hook，不要在頁面留下無界限 buffer。
7. 未經使用者要求，不送聊天、擲骰或其他會改變遊戲狀態的協議。

## 判讀原則

- 聊天歷史不包含每題 `question_id`；活動結束後不能聲稱已從歷史唯一還原題目。
- 只用 `configQuiz.answers`／`options` 的精確匹配判定答案，避免 `5` 誤命中 `50`。
- 同秒訊息順序、錯字、同題多次猜測都要標示不確定性。
- `ext_list` 是角色呈現資料，不是題目 payload。
- 骰子階段與競答共用家族聊天，需以時間、系統內容和 link type 區分。

## 交付格式

回報時至少包含：

- 裝置與實際 CDP port。
- 抓到的 cmd、方向、payload 長度或欄位。
- 題目／答案是即時確定、題庫精確匹配，還是事後推測。
- 排名 JSON、家族總分和完成時間（若存在）。
- listener／hook 是否已清理。
