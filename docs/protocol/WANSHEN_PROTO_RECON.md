# 萬神試煉（Rogue）純 WS 協議研究筆記 — emulator-5560

- 日期：2026-08-07（週五，萬神開放日 Mon-Sat）
- 帳號：uid=27413558（role 89562953027833），plat=2002（TW）
- 方式：`ws_token/client.py` 純 WebSocket 連線 + B 頁（`battle_calc/ephemeral_b.py` protoRoot）decode 驗證
- 目的：確認 cmd 流程、欄位語義（特別是本週開局/存檔機制），補充 `ROGUE_PROTO_SCHEMA.json`

## 1. 協議流程（一次完整局）

登入（`auth` 握手）後依序：

| 順序 | C2S cmd | S2C | 說明 |
|------|---------|-----|------|
| 1 | 0x4C01 INFO | 0x4C01 `rogue_info_s2c` | 本週資訊（id/point/score/rank/get_list） |
| 2 | 0x4C20 STATUS | 0x4C20 `rogue_status_s2c`（2B） | field1: 1 = 有進行中局 |
| 3 | 0x4C03 OVER | 0x4C03 `rogue_main_over_s2c` | 結束「舊局」（上一個進行中局），回傳該局 `rogue_report` |
| 4 | 0x4C02 ENTER | 0x4C02 `rogue_main_enter_s2c` | 開新局，回傳其他資訊/技能/獎勵預覽等 |
| 5 | 0x4C24 START_REWARD_INFO | 0x4C24 | 開局獎勵資訊 |
| 6 | 0x4C26 START_REWARD_CONFIRM | 0x4C26 | 領取開局獎勵 |
| 7 | 0x4C04 COMBAT | 0x4C04 `rogue_main_combat_s2c` | 打一關：code/seed/atk_data/def_data |
| 8 | 0x4C05 RESULT | 0x4C05 `rogue_main_result_s2c` | 送結果（win/precent），回報酬 reward_list |
| 9 | （可重複 7-8） | | 繼續打下一關 |
| 10 | 0x4C03 OVER | 0x4C03 | 結束本局，回傳 `rogue_report` 結算 |

PUSH（server 主動推）：狀態變更即推，同 cmd 同結構：
- 0x4C01 局狀態變更（id/coin/score）
- 0x4C07 背包變更（`rogue_bag_s2c`，27 個 {gtid,num}）
- 0x4C09 屬性升級（`rogue_attr_up_s2c`）
- 0x4C16 科技樹（`rogue_science_info_s2c`）
- 0x4C20 局狀態（進行中 1）

## 2. 關鍵欄位語義（實測確認）

### 2.1 `rogue_info_s2c`（0x4C01）
```
id        int32   本週存檔點/目前進度（見 §3）
point     int32   實測恆為 0；point=0 仍可正常開局打關 → 不是「剩餘次數」
end_time  int64   本週結束時間（epoch sec）
score     int64   本週累計積分（排行榜積分，局結束 += 局內分數）
get_list  [int]   每週目標清單 [1..10]（固定 10 個）
rank      int32   排行榜名次（本帳號 151）
attr_list [kv]    本週累計屬性（{attr_id, value}，raw walk 有值；
                  B 頁 proto decode 的 attr_list 元素顯示全 0，是 B 頁 proto
                  resolvedType 缺失所致，decode 不可信，以 raw walk 為準）
```

### 2.2 `rogue_main_combat_s2c`（0x4C04）
```
code      int32  0 = ok
seed      int32  隨機種子（兩次實測 5649 / 7843，每關不同）
atk_data  Role   我方（name/lev/job/role_skill...）lev=194（玩家等級）
def_data  Role   敵方（31 級模板）
```

### 2.3 `rogue_main_result_s2c`（0x4C05）
```
code      int32   0 = ok
is_win    int32   1 = 勝利
precent   int32   0（勝率/進度百分比）
def_role  Role    敵方角色
reward_list [gtid,num]  過關獎勵（如 [(1,5),(2041,1)] 或 [(1,4),(2141,1)]）
```

### 2.4 `rogue_main_over_s2c`（0x4C03）→ `rogue_report`
```
report_id  int32  局編號（連續，實測 449-454）
level      int32  本局結束層（= 結束時的 id，見 §3）
score      int32  本局獲得積分
is_coll    int32  是否已領
time       int64  結束時間
precent    int32  100（完成度）
hp         int32  剩餘血量
coin       int32  局內金幣（160 = PUSH 0x4C01 field2）
branch     int32  分支（0）
mirror     int32  鏡像（0）
old        int32  0
item_list  [kv]   局內獲得道具（raw walk 有值；decode 全 0 同 attr_list 問題）
```

## 3. 開局/存檔機制（本次研究的核心發現）

### 3.1 id 變化實測

三次研究（每次 = 結束舊局 → 開新局 → 打 N 關 → 結束）：

| 局 | 開局點 | PUSH id 序列（每贏一關 +1） | 結束層 | 結束後存檔 |
|----|--------|------------------------------|--------|------------|
| v1 bot 局（over_old 結束） | 31 | 進行中 id=36 | 36 | 31 |
| v1 研究局 | 31 | 31→32 | 32 | 31 |
| v2 bot 局（over_old 結束） | 21 | 進行中 id=26 | 26 | 21 |
| v2 研究局 | 21 | 21→22 | 22 | 21 |
| v3 bot 局（over_old 結束） | 16 | 進行中 id=21 | 21 | 16 |
| v3 研究局 | 16 | 16→17→18→19→20→21→22（6 關） | 22 | 21 |

### 3.2 規則

1. **存檔點 = 5k+1**（…11, 16, 21, 26, 31, 36, 41, 46…），本週從存檔點開打
2. **局內 id = 開局點 + 已通過關數**：開局時 id = 存檔點，每打贏一關 id+1
3. **結束存檔 = 開局點 + floor((通過關數 - 1) / 5) × 5**
   - 等價：通過 1-5 關 → 存檔 = 開局點（不推進）
   - 通過 6-10 關 → 存檔 = 開局點 + 5（推進一次）
   - 通過 11-15 關 → 開局點 + 10，依此類推
4. `over_old`（強制結束進行中的舊局）與正常 `OVER` 結算規則相同

### 3.3 對應玩家觀察（已由實測驗證）

- 「36-40(含) 這場結束後會回到 36 開始打」：從 36 開打，通過 36..40（5 關）
  → floor(4/5)=0 → 存檔 36 ✓（36-40 為一組，組頭 36）
- 「打贏 41，下次會從 41 開始」：通過 41（第 6 關）→ floor(5/5)=1 → 存檔 36+5=41 ✓

## 4. 其他觀察

- `start_reward_info_s2c`：每次開局獎勵包不同（與帳號進度相關）；
  本次開局獎勵 [{gtid,num}] 27 個（含 gtid=1 金幣 104~155、gtid=3 35~47）
- `status_s2c` 只有 1 個欄位（1B）：0 = 無局 / 1 = 有進行中局
- 局結束積分：一局約 345~474（與打的關數/層數正相關但無固定線性）
- 週結束時間 end_time=1786291200 = 2026-08-09（週日）

## 5. 研究注意事項（踩過的坑）

1. **stdout 與 JSONL 交錯會損壞記錄**：print body 到 stdout + 寫檔同時進行時，
   stdout 的 TextIOWrapper 緩衝會與檔案寫入交錯，導致 JSONL 部分行損壞；
   研究腳本應只寫檔、stdout 只印摘要。client.close() 後 PUSH 執行緒仍在寫檔
   會截斷最後一行，解析時需容忍尾行錯誤。
2. **B 頁 Type.decode 對「伺服器 schema 較新」的巢狀欄位（attr_list/item_list）
   會顯示全 0**（resolvedType 缺失），此時以自訂 protobuf raw walker 為準。
3. decode 時用 `Type.toObject(msg, {longs:Number, enums:String, bytes:String})`，
   直接 JSON.stringify(msg) 會 TypeError。
4. `launch_ephemeral_b` 不接位置參數（全部 keyword）。
5. 同帳號同時段只能一個 WS 連線做會話：研究期間避免 bot 同時跑 5560，
   否則互頂（LoginConflict 型錯誤）。

## 6. 與既有實作的關係

- `ws_token/rogue.py`：cmd 常數與 build 函式正確，可直接用
- `ws_token/rogue_fight.py`：純 WS 打關（live-verified 於 7fe98fc6）
- `ROGUE_PROTO_SCHEMA.json`：欄位名正確；attr_list/item_list 元素型別
  在 B 頁 proto 中解析失敗屬 B 頁 proto 版本問題，不影響實作
