# 郵件/信箱 (Mail) 協議 (2026-06-14, 純 WS LIVE 驗證)

> 實驗環境：emulator-5554，bot launched via dashboard，CDP attach via 9230，Cocos Creator
> 3.6.3 + protobuf-over-WebSocket。cmd id 由 `docs/game_client_sources/index.966f5.js` 的
> c2s/s2c 名稱→編號表（line 7835）抽出，body/回應結構由真機 `netManager._cnet.reciveMsg`
> sniff（`tools/_mail_recon.py`）round-trip 驗證。

## Cmd Family（mail 模組 = 21；cmd = 21*256 + N）

| Hex (dec) | Name | 方向 | 用途 |
|-----------|------|------|------|
| **`0x1501`** (5377) | **`mail.mail_list_c2s/s2c`** | 雙 | **列郵件**（c2s 帶 {mail_id}，mail_id=0=全部） |
| `0x1502` (5378) | `mail.mail_new_c2s/s2c` | s2c | 新郵件 push（單封 mail_info） |
| `0x1503` (5379) | `mail.mail_read_c2s/s2c` | 雙 | 標記已讀（mail_id=0=全部） |
| **`0x1504`** (5380) | **`mail.mail_claim_c2s/s2c`** | 雙 | **領取附件**（mail_id=0 = 一鍵領取全部） |
| `0x1505` (5381) | `mail.mail_delete_c2s/s2c` | 雙 | 刪除（mail_id=0=全部） |
| `0x1506` (5382) | `mail.mail_expired_reward_c2s/s2c` | 雙 | 領過期郵件補償 |
| `0x0201` (513) | `error.error_info_s2c` | s2c | 通用錯誤通道 |

來源（JS line 7835 c2s 表，s2c 表編號相同）：
```
"mail.mail_list_c2s":5377,"mail.mail_new_c2s":5378,"mail.mail_read_c2s":5379,
"mail.mail_claim_c2s":5380,"mail.mail_delete_c2s":5381,"mail.mail_expired_reward_c2s":5382
```

`MailControl` (line 7217) 的 c2s builder：
```js
reqMailList(i)        -> netManager.send("mail.mail_list_c2s",   {mail_id:i})
reqReadMail(i)        -> netManager.send("mail.mail_read_c2s",   {mail_id:i})
reqGetMailGood(i,e)   -> netManager.send("mail.mail_claim_c2s",  {mail_id:i, type:e})
reqDelMail(i,e)       -> netManager.send("mail.mail_delete_c2s", {mail_id:i, type:e})
```
遊戲內「一鍵領取」按鈕 → `reqGetMailGood(0,0)`（mail_id=0、type=0）。

## Request / Response schemas（live-verified 5554）

### `0x1501` mail_list

```proto
message MailListReq  { uint64 mail_id = 1; }   // ⚠ 必填；空 body 回空（已驗證）
message MailListRsp  { repeated PMail mail_list = 1; }
```

- **空 body（不帶 mail_id）→ 回 cmd 5377 但 body 長度 0**（誤判「無信」）。
- 帶 `{mail_id:0}`（wire `0800`）→ 回 4883B，repeated f1 = 50 封信。

`p_mail`（每封信，live 解出欄位）：

| field | 名稱 | 型別 | 說明 |
|-------|------|------|------|
| 1 | id | uint64 | 郵件 id（claim/read/del 用） |
| 2 | cfg_id | uint32 | `configMail` key（→ 標題/內文/有效期 30 天） |
| 3 | ? | uint32 | 0（未用） |
| 5 | title_ref | msg{f1} | 標題字串 id / arg 容器 |
| 6 | content | msg{f1:id, f2:arg...} | 內文字串 id + 模板參數 |
| 7 | send_time | uint32 | 寄送時間 unix sec |
| 8 | exp_time | uint32 | 到期時間 unix sec（= send_time + 30 天） |
| 9 | is_read | uint32 | 0/1 |
| **10** | **is_attach** | uint32 | **1 = 有未領附件、0 = 無附件/已領** |
| 11 | goods_list | repeated msg{item_id#1, num#2} | 附件物品 |

> 注意：`title`/`content` 是物件（含 `arg_list` 模板參數），非純字串；自動領取只需要 `id` 與
> `is_attach`，不依賴文字。

### `0x1504` mail_claim

```proto
message MailClaimReq { uint64 mail_id = 1; uint32 type = 2; }  // mail_id=0 = 全部；type 一律 0
message MailClaimRsp { repeated uint64 claim_list = 1; repeated PReward goods_list = 2; }
```

LIVE 行為（5554，現況 is_attach=1 = 0 封）：
- 送 `5380 {mail_id:0, type:0}`（wire `08001000`）→ **回 cmd 5380 但 body 長度 0**（無可領）。
- 無附件可領時 server 回空 body、**不報 0x0201**（安全 idempotent，可每次喚醒重送）。
- 有附件時回 `{claim_list:[領到的 mail id...], goods_list:[獎勵...]}`（依 client `mail_claim_s2c` 模型，line 7223；本次無可領未抓到 success body）。

## 容量「滿」偵測（武魂 / 神器附魔寶石）— 結論：client 無硬 cap

使用者要求偵測「武魂滿 / 神器附魔寶石滿」以便信件附件溢位時跳過。實際 recon 結論：

### 神器附魔寶石（artifact_gem，module 53）
- cmd `0x3501` (13569) `artifact_gem_info_s2c`：top-level **f2 repeated = 每顆寶石**。
  gem entry: `{id#1:uint64, quality#2, level#3, pos#4(101-107槽位), lock#7?, equipped#8?, attrs...}`。
- 5554 live **gem_count = 2515**。
- `artifact_gem_max_num:500`（global config, line 5017）是 **dead config key**：整包 JS 只出現 1 次（定義處），
  **client 從不讀取、無任何 `count>=max` 比較、無「倉庫已滿」UI**。帳號 2515 顆遠超 500 卻照常運作，
  證實 500 不是硬牆（若有約束亦為 server 端，client 不擋）。

### 武魂（skill，module 8）
- cmd `0x801` (2049) `skill_list_s2c`：f1 repeated = 作用中技能 `{pos_id#1, skill_id#2, skill_lv#3}`；
  **f2 repeated = 持有武魂道具 `{item_id#1, count#2}`**（5554 live 51 種）。
- 整包 JS **無 `skill_max_num`/`wuhun_max` 之類 cap**；武魂走 upgrade/compose 消耗，client 無「滿」概念。

### 因此本任務採用
1. capacity helper 只負責 **讀現量**（gem 數 / 武魂道具種數），並提供 `is_gem_full(threshold)` /
   `is_skill_full(threshold)` 的 **best-effort 門檻檢查**（門檻可設；預設用 `artifact_gem_max_num=500`
   作參考值，但已知非硬牆）。
2. 真正的溢位防護靠 **server 的 mail_claim 行為**：無可領回空、不報錯；即使附件含寶石/武魂，server
   為權威端。scheduler 在門檻命中時 **log 警告並照常領取**（不阻擋），因為 client cap 不可信、
   且漏領比重複嘗試更糟。若日後 server 真的回某 error_code 表示「背包滿」，於 `mail.py` 的
   0x0201 處理加碼即可。
