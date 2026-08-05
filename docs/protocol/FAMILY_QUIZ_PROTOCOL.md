# 家族趣味競答協議與 CDP 取證

## 目錄

- [用途與結論](#用途與結論)
- [頻道與資料流](#頻道與資料流)
- [協議索引](#協議索引)
- [型別結構](#型別結構)
- [題庫解析](#題庫解析)
- [CDP 抓取流程](#cdp-抓取流程)
- [實測紀錄](#實測紀錄)
- [限制與判讀規則](#限制與判讀規則)
- [相關檔案](#相關檔案)

## 用途與結論

本文件是「家族趣味競賽」、「家族趣味競答」、「家族競答」、「家族智多星」及相關聊天封包的單一協議索引。

最重要的結論：

1. 家族聊天使用 `channel = 3`。
2. 聊天歷史 `chat.chat_history_s2c` 只保存玩家輸入、系統提示和結算分享，不保存每題的 `question_id`。
3. 題目由即時協議 `guild.guild_question_s2c` 下發；客戶端以 `question` 查 `configQuiz` 才得到題目、選項和合法答案。
4. 活動結束後 `GuildDataCache.guildQuestion` 會清成零。若活動期間沒有攔截 cmd `7446`，事後不能只靠聊天歷史百分之百重建全部題目。
5. 結算訊息「本期家族智多星已產生」的 `p_link.type = 7`，其 `string_list[0]` 是 JSON，包含個人分數、家族排名、家族總分和完成時間。

## 頻道與資料流

客戶端 `ChatDefine.Channel` 的核心值：

| 頻道 | 值 |
|---|---:|
| World | 1 |
| Private | 2 |
| Guild（家族） | 3 |
| News | 5 |
| Cross | 6 |
| DragonRealmTeam | 31 |
| DragonRealmCross | 32 |
| ParkingCross | 39 |

競答資料流：

```text
guild.guild_question_s2c (7446)
  question -> configQuiz.getDataByKey(question)
  number / is_show / countdown / rank_list
              |
              v
玩家在家族頻道作答
chat.chat_message_c2s (1537, channel=3)
              |
              v
chat.chat_message_s2c (1537) / chat.chat_history_s2c (1538)
              |
              v
系統結算分享 p_link.type=7 -> string_list[0] JSON
```

## 協議索引

### 聊天協議

| cmd | C2S / S2C | 欄位 | 用途 |
|---:|---|---|---|
| 1537 | `chat.chat_message_c2s` | `channel:uint32(1)`, `target_id:uint64(2)`, `content_type:uint32(3)`, `content:string(4)`, `links:p_link[](5)`, `args:p_key_value_string[](6)` | 發送聊天／競答文字 |
| 1537 | `chat.chat_message_s2c` | `channel:uint32(1)`, `target_id:uint64(2)`, `chat_info:p_chat(3)` | 即時聊天推送 |
| 1538 | `chat.chat_history_c2s` | `channel:uint32(1)`, `target_id:uint64(2)` | 查詢頻道歷史 |
| 1538 | `chat.chat_history_s2c` | `channel:uint32(1)`, `target_id:uint64(2)`, `chat_history:p_chat[](3)` | 回傳歷史訊息 |
| 1542 | `chat.chat_channel_list_c2s` | 無 | 查詢額外開放頻道 |
| 1542 | `chat.chat_channel_list_s2c` | `chat_channel_list:uint32[](1)` | 額外頻道列表；基礎家族頻道不一定列在此陣列 |

家族歷史查詢的已驗證 payload：

```text
cmd 1538
TX  len=4  hex=08 03 10 00
               ^^ channel=3
```

2026-08-05 的 5560 實測回應為 cmd `1538`、長度 `11786` bytes、共 `121` 筆 `p_chat`。

回應開頭已驗證為：

```text
08 03 10 00 1a 77 08 00 12 06 08 00 10 00 1a 00 ...
^^ channel=3     ^^ repeated chat_history field
```

同次 `chat_channel_list_s2c` 回傳 `[32, 39, 6, 31]`；這些是額外開放頻道，家族基礎頻道 `3` 不在陣列中。

### 家族競答協議

| cmd | C2S / S2C | 欄位 | 用途 |
|---:|---|---|---|
| 7446 | `guild.guild_question_c2s` | 無 | 進入家族聊天時查詢當前題目 |
| 7446 | `guild.guild_question_s2c` | `question:uint32(1)`, `number:uint32(2)`, `is_show:uint32(3)`, `countdown:uint32(4)`, `rank_list:p_guild_question_rank[](5)` | 下發當前題目 ID、題號、顯示狀態、截止時間與排名 |
| 7447 | `guild.guild_question_rank_c2s` | 無 | 查詢競答排名 |
| 7447 | `guild.guild_question_rank_s2c` | `rank_list:p_guild_question_rank[](1)` | 更新家族競答排名 |
| 7457 | `guild.guild_dice_start_c2s` | 無 | 查詢／啟動競答後骰子階段 |
| 7457 | `guild.guild_dice_start_s2c` | `countdown:uint32(1)` | 骰子階段截止時間 |
| 7458 | `guild.guild_dice_point_c2s` | 無 | 擲骰子 |
| 7458 | `guild.guild_dice_point_s2c` | `point:uint32[](1)`, `reward_list:p_reward[](2)` | 骰子結果與獎勵 |

客戶端控制流程已確認：開啟家族聊天會呼叫 `send_29_22()`（cmd `7446`）與 `send_29_33()`（cmd `7457`）。

### 聊天分享 link type

| `p_link.type` | 實測語意 |
|---:|---|
| 4 | 一般系統／骰子結果提示 |
| 6 | 「家族競答活動將在 5 分鐘後開啟」 |
| 7 | 「本期家族智多星已產生」結算；`string_list[0]` 為 JSON |
| 8 | 「第 N 道題目回答結束」 |
| 11 | 家族紅包消息 |

`type=7` 結算 JSON：

```json
{
  "rank_list": [
    {
      "name": "玩家名",
      "score": 8,
      "head_id": 0,
      "frame_id": 0,
      "url": "頭像 URL"
    }
  ],
  "guild_rank": 1,
  "guild_score": 30,
  "guild_time": 103
}
```

`guild_time` 與實測答題區間吻合，單位為秒。

## 型別結構

### `type.p_chat`

| field id | 名稱 | 型別 |
|---:|---|---|
| 1 | `role_id` | `uint64` |
| 2 | `head` | `p_head` |
| 3 | `name` | `string` |
| 4 | `gender` | `uint32` |
| 5 | `content` | `string` |
| 6 | `server_id` | `int32` |
| 7 | `time` | `int32`，Unix 秒 |
| 8 | `type` | `int32`；實測 `1=文字`、`2=表情/特殊內容`、`3=系統/分享` |
| 9 | `is_block` | `uint32` |
| 10 | `links` | `p_link[]` |
| 11 | `ext_list` | `p_chat_elem[]` |

### `type.p_chat_elem`

| field id | 名稱 | 型別 |
|---:|---|---|
| 1 | `field` | `uint32` |
| 2 | `num` | `int64` |
| 3 | `string` | `string` |

競答聊天中觀察到的 `ext_list` 是角色呈現資料，不是題目資料。客戶端 `GenChatData()` 會把它轉成 `title`、`bubble`、`guildCareer` 等欄位。例如 5560 玩家訊息的值為 `title=45`、`bubble=2001`、`guildCareer=1`。

### `type.p_link`

| field id | 名稱 | 型別 |
|---:|---|---|
| 1 | `pos` | `uint32` |
| 2 | `type` | `uint32` |
| 3 | `args_list` | `uint64[]` |
| 4 | `string_list` | `string[]` |

### `type.p_guild_question_rank`

| field id | 名稱 | 型別 |
|---:|---|---|
| 1 | `rank` | `uint32` |
| 2 | `guild_id` | `int64` |
| 3 | `guild_name` | `string` |
| 4 | `score` | `int64` |

## 題庫解析

題目不直接放在聊天內容，而是由 cmd `7446` 的 `question` 查全域表 `configQuiz`：

```javascript
const row = configQuiz.getDataByKey(questionId);
row._data;
```

`_data` 的已驗證排列：

```text
[question_id, question_text, type, options, answers]
```

- `type = 1`：選擇題，`options` 有四個選項。
- `type = 2`：文字題，`options = null`，`answers` 是伺服器接受的字串集合。
- 判斷答案時要做「合法答案精確匹配」，不要只用子字串。例如 `5` 會誤命中題號、題目文字或 `50`，但它不是任何已知合法答案。
- 玩家可以在同一題期間送出多個普通聊天訊息；聊天歷史本身不標示哪一筆被判定正確。

## CDP 抓取流程

### 1. 找裝置 port

永遠先讀 `bot_config.json -> devices.<device_id>.web_debug_port`，不要永久假設 port。2026-08-05 實測：

| 裝置 | CDP port |
|---|---:|
| `emulator-5560` | 9225 |
| `emulator-5554` | 9230 |

確認 target：

```powershell
Invoke-RestMethod http://127.0.0.1:<port>/json/list | ConvertTo-Json -Depth 2
```

依 repo 規範使用 `mushroom1` 環境：

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 `
  python tools\rawcdp.py --port <port> --expr "document.title"
```

### 2. 事後讀家族聊天

先註冊一次性 listener，再送歷史查詢：

```javascript
(() => {
  window.__family_history = null;
  const cb = (m) => {
    if (m.channel !== 3) return;
    window.__family_history = (m.chat_history || []).map((c) => ({
      role_id: String(c.role_id),
      name: String(c.name || ""),
      content: String(c.content || ""),
      time: c.time,
      type: c.type,
      ext_list: c.ext_list || [],
      links: c.links || [],
    }));
  };
  window.__family_history_cb = cb;
  netManager.addEventListener("chat.chat_history_s2c", cb, window);
  netManager.send("chat.chat_history_c2s", {channel: 3, target_id: 0});
  return true;
})()
```

讀完必須移除 listener：

```javascript
(() => {
  const result = window.__family_history;
  if (window.__family_history_cb) {
    netManager.removeEventListener(
      "chat.chat_history_s2c",
      window.__family_history_cb,
      window
    );
  }
  return result;
})()
```

若歷史已載入，可直接讀客戶端 cache：

```javascript
(async () => {
  const mod = await System.import("chunks:///_virtual/ChatDataCache.ts");
  return IS(mod.ChatDataCache).GetChatInfo(3, 0);
})()
```

### 3. 活動期間攔截題目

必須在競答進行時監聽 cmd `7446` 對應事件：

```javascript
(() => {
  window.__family_quiz_trace = window.__family_quiz_trace || [];
  const cb = (m) => {
    const q = configQuiz.getDataByKey(m.question);
    window.__family_quiz_trace.push({
      captured_at: Date.now(),
      question_id: m.question,
      number: m.number,
      is_show: m.is_show,
      countdown: m.countdown,
      rank_list: m.rank_list || [],
      quiz: q ? q._data : null,
    });
  };
  window.__family_quiz_cb = cb;
  netManager.addEventListener("guild.guild_question_s2c", cb, window);
  netManager.send("guild.guild_question_c2s", {});
  return true;
})()
```

活動結束或分析完成後移除 `window.__family_quiz_cb`。長時間監聽時要限制陣列長度，避免頁面記憶體無限增加。

### 4. 讀目前題目 cache

```javascript
(async () => {
  const mod = await System.import("chunks:///_virtual/GuildDataCache.ts");
  const current = IS(mod.default).GetGuildQuestion();
  return {
    current,
    quiz: current && current.question
      ? configQuiz.getDataByKey(current.question)._data
      : null,
  };
})()
```

活動結束後通常會得到 `question=0, number=0, countdown=0`，這不是抓取失敗，而是客戶端已清除狀態。

## 實測紀錄

### 5560：2026-08-05 19:07–19:10

- 歷史回應 121 筆，其中系統訊息 115 筆、一般文字 6 筆。
- 只有「夜露死苦」參與後段作答。
- 可從題庫精確匹配：`蘭花`、`大熊座`、`10`。
- `守衛殘桓` 意圖回答「殘垣古城／守衛殘垣古城」，但字形錯誤且缺少「古城」。
- `5` 不匹配 116 題題庫的任何合法答案或選項。
- `1515` 出現在第 10 題結束後，下一秒系統顯示「夜露死苦投出了15點」，因此是骰子階段，不是競答答案。

### 5554：2026-08-05 19:05–19:10

歷史查詢回傳最新 `100` 筆：文字 `76` 筆、特殊內容 `2` 筆、系統／分享 `22` 筆，時間範圍為 18:52:35–19:26:01。

從聊天時間窗與 `configQuiz` 重建的題目：

| 題號 | question id | 題目摘要 | 合法答案 |
|---:|---:|---|---|
| 1 | 97 | 氣態直接變固態 | 凝華 |
| 2 | 8 | 大珠小珠落玉盤的樂器 | 琵琶 |
| 3 | 104 | 沒有鬍子的老 K | 紅心K／紅心老K／紅桃K |
| 4 | 60 或 67 | 紫土地或麻婆豆腐起源省份 | 四川／四川省 |
| 5 | 39 | 花中第一流 | 桂花／木犀／木犀花 |
| 6 | 38 | 臥薪嚐膽人物 | 勾踐 |
| 7 | 65 | 四君子缺少的花 | 蘭花／蘭 |
| 8 | 115 | 北斗七星天文名稱 | 大熊星座／大熊座 |
| 9 | 54 | 遺物碎片副本 | 殘垣古城／守衛殘垣古城 |
| 10 | 2 | 第一次轉職等級 | 15／15級／十五／十五級 |

第 4 題只有答案「四川」且活動後題目 ID 已清除，因此保留兩個候選，不能假裝已確定。

結算 `p_link.type=7` 實測：

- `guild_rank = 1`
- `guild_score = 30`
- `guild_time = 103`
- 花葉雛菊 8 分
- 寶兒࿐ 8 分
- ～哇鯊咪～ 6 分

## 限制與判讀規則

1. 不要把聊天答案反推成唯一題目，除非該答案在 `configQuiz.answers` 只有一個精確匹配；「四川」就是已知歧義例。
2. 同一題可出現多個猜測，歷史封包沒有 `correct` flag。排名／總分只能驗證整體結果，不能可靠標記每一筆答案。
3. 同秒的玩家訊息和「回答結束」訊息排序不足以證明答案是否在截止前被伺服器接受。
4. `ext_list` 是角色聊天呈現資料，不含題目 ID。
5. `chat_channel_list_s2c` 回傳的是額外開放頻道；不要因列表沒有 `3` 就判定沒有家族頻道。
6. 歷史訊息通常有數量上限；舊的題目結束提示可能被截斷。
7. 競答後骰子訊息使用相同家族聊天頻道，分析時要用時間與 `p_link.type` 排除。
8. 分析預設只做讀取：可送 `chat_history_c2s`、`guild_question_c2s`、`guild_question_rank_c2s`；未經要求不要送 `chat_message_c2s` 或 `guild_dice_point_c2s`。

## 相關檔案

- `tools/rawcdp.py`：最小 raw-CDP `Runtime.evaluate` client。
- `utils/ws_listener.py`：頁內 WebSocket cmd／body ring buffer。
- `docs/protocol/GUILD_PROTO_SCHEMA.json`：7446–7458 家族協議 schema。
- `docs/protocol/TYPE_PROTO_SCHEMA.json`：`p_chat`、`p_chat_elem`、`p_link`、`p_guild_question_rank` schema。
- `bot_config.json`：各裝置 `web_debug_port`。
