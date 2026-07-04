# 秘寶 (尋寶) WS recon — secret_jewel module 85 (2026-06-27, CDP 5556 9223)

新改版功能「秘寶」(與守護靈同頁的 `JewelView`)。**全部 live 重新抓取** (CDP 5556)，不沿用舊 draw schema。
目前只有 **塵世秘寶 pool_type=1** 開放，傳說(2)/遠古(3) 鎖定。

## 家族 / cmd (fake-cnet 權威解，capture-only 不送出)

`secret_jewel` **module 85**；c2s/s2c 共用 id；失敗走 `0x0201` error_code (= 513)。

| msg | cmd | c2s | s2c |
|-----|-----|-----|-----|
| secret_jewel_info | **21761** | `{}` | `{ jewel_list#1:p_secret_jewel_info[], suit_list#2:p_secret_jewel_suit[], pool_list#3:p_secret_jewel_pool[], ext#4:p_key_value[] }` |
| secret_jewel_draw | **21764** | `{ pool_type#1:uint32, count#2:uint32 }` | `{ pool#1:p_secret_jewel_pool, reward_list#2:p_reward[] }` |
| secret_jewel_star_up | 21762 | `{ jewel_id#1 }` | `{ update_jewel#1:p_secret_jewel_info }` |
| secret_jewel_level_up | 21763 | `{ jewel_id#1 }` | `{ update_jewel#1:p_secret_jewel_info }` |

> 注意：`netManager._protoClass` 只快取「用過的」訊息 (lazy `protoRoot.lookup`)，完整 schema 必須走 `protoRoot.lookup('secret_jewel').nested`。
> 注意：掛機戰鬥的觸控同步是 **module 13 (cmd 3331/3332)**，body f3≈{x,y}，emit/點擊時會混進來 → sniff 時要過濾。

### types (protoRoot)
```
p_secret_jewel_pool { pool_type#1:uint32, free_times#2:uint32, must_info#3:p_key_value[] }
p_secret_jewel_info { jewel_id#1, star#2, level#3, condition_count#4:p_key_value[] }
p_secret_jewel_suit { suit_id#1, suit_level#2 }
p_reward            { gtid#1:int32, num#2:int64 }
p_key_value         { k#1:int64, v#2:int64 }
```
- `free_times` = 該池**今日剩餘免費抽** (= 2，使用者「每天免費兩次」)。
- `must_info` = `[{k=1, v=累計抽卡數}]` → pity 計數，**100 抽必出稀有秘寶** (UI「再尋寶N次，必定出現」)。free+paid 共用此計數。
- 與 `spirit` 的 `p_spirit_draw {draw_id, free_times, must_info}` 同構。

## 抽卡 (尋寶) — secret_jewel_draw 21764 (live 5556 verified)
- **免費單抽** `draw{pool_type=1, count=1}` while `free_times>0`：零花費，server push reward + free_times--。
- **付費單抽** `count=1` while `free_times==0`：扣 **尋寶圖 (item 1340) ×1**；reward = 秘寶碎片 (gtid 1347/211002 等，每抽 5 個)。
- **十連** `count=10`：扣尋寶圖 ×10 (UI「尋寶10次」cost 7/10 = 持有7/需10)。
- 0x0402 推送：event_type 20016005=消耗、20016006=獲得。
- 拒絕走 0x0201：(沿用 configErrorInfo) 159=次數不足 等。

## 每日購買尋寶圖 — shop_buy 6914 (module 27，與守護靈招喚貨幣同條) ★購買在右上角 `btnBuy`
- 右上角「每日 10/10」`JewelLotteryView/btnBuy` → 開 `MallTipsView` 確認框 → `MallTipsView/btnBuy` 送：
  - **`shop_buy` 6914 `{ shop_type#1=26, shop_id#2=2600001, num#3 }`** → 每買 1 給 1 尋寶圖(1340)。
- `shop_info` **6913** `{shop_type=26}` → `{ shop_type#1, buy_list#2:{shop_id#1, bought_count#2}[] }` → 讀今日已買數。
- configMall 2600001 = `goods=[1340,1], cost=[2,600], daily_limit=10`：每個 **600 (貨幣類型2，疑似鑽石)**，**每日上限 10**。
  - 「每天買10個」= shop_buy(26, 2600001) 補到 10/日 (讀 6913 已買數，買 10-bought)。server 端 enforce 上限，超量回 0x0201。

## config 表
- `configJewel_draw` (每池一列): col0=pool_type, col5=pity `[[1,[2],100,211006]]`(100抽必出211006), **col7=`[1340,1]` 成本尋寶圖×1**, **col8=2 每日免費**, col9=品質權重。
- `configMall.getDataByKey(2600001)` = 尋寶圖商城項 (見上)。
- 名稱: 1340=尋寶圖, 209085=塵世秘寶, 209093=尋寶圖。

## 使用者需求 → 實作 (兩個獨立可選開關)
1. **是否購買每日10/10** = shop_buy(26, 2600001) 補到每日 10 (opt-in，**花鑽石**，預設關)。
2. **是否抽每日免費** = secret_jewel_draw pool_type=1 count=1 × free_times (免費，預設可開)。

模板：`ws_token/spirit.py` (draw_all_free + buy_summon_currency) 幾乎一模一樣。
