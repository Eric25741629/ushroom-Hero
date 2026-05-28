# 挖礦道具策略 sim A/B

## 目標
量化:「鏟子稀缺(~15/場)+ 道具囤貨(數百)」下,積極用道具能不能在同一個 6 分鐘窗口
內**完成更多礦洞 (cluster) = 更多礦石**。礦石只在整個 cluster 全清時給(已確認 = sim 規則)。

## 經濟前提(user 已確認)
- 鏟子:唯一來源 regen +1/6min(慢);每場約 15 把見底。
- 道具(炸彈/鑽頭):唯一來源是挖到礦洞;已囤 460~900,長期淨增。
- 礦洞稀缺(75~87% 版面 no_pit),不該略過。
- 每場 6 分鐘 ≈ 45 個動作(每動作 ~7-8s)。

## 做法
- [x] 讀 mining_sim_eval.py + v4 planner 成本介面(成本是 module global,可 monkeypatch;無測試 import harness)
- [ ] play_one_game 加 `action_budget`(模擬 6min 牆鐘)+ 回傳 clusters/actions/半清浪費(向後相容)
- [ ] 新 driver tools/mining_ore_ab.py:monkeypatch v4 成本跑 A/B/C
  - A 現況保守 drill=2.5 bomb=3.0
  - B 道具便宜 drill=0.5 bomb=0.5
  - C 近乎免費 drill=0.1 bomb=0.1
  - 真實庫存 pickaxe=15 bomb=700 drill=460;固定 seed;action_budget=45
- [ ] 跑 30 seeds,比 clusters 完成(1/4/9)、ore cells、動作數、道具消耗、stuck、半清浪費
- [ ] 回報數字 + 解讀(注意:sim 不含真機 OCR=0 / f7 提早放棄 bug,sim A 會比真機 A 積極)

## Review (2026-05-25)

跑完 30 seeds。**結論推翻了「調 cost model」的假設**。

### 礦石(ore)≈ 線性正比於每場動作數(config A 保守)
| action budget | ore 格 | 礦洞數 | 動作 |
|---|---|---|---|
| 15 (真機被 bug 卡住的現況) | 32.0 | 7.9 | 15 |
| 30 | 62.6 | 15.0 | 29.5 |
| 45 (用滿 6min) | 90.7 | 21.9 | 43.1 |

→ 15→45 動作 = ore **32 → 90.7 = ~2.8x**。每動作 ~2 ore 格。**最大槓桿是「每場跑幾個動作」,不是 cost model。**

### Cost model 幾乎沒差
同 budget 下 A vs B(道具便宜)vs C(近免費):ore 只差 +1~4%。便宜道具只把鏟耗砍半(10.7→5.8),但鏟子不是 user 在乎的東西。**不值得改 cost model。**

### 其他假設被否證
- 半清浪費 ~2 格/場(可忽略)。commit-or-skip 計分有效。
- sim 裡 A(保守)本來就用 ~26 鑽+7 炸/場、跑滿 43 動作 → planner 意圖正確;**真機只用 ~2 鑽是被 bug 擋住**(OCR=0、f7 提早放棄)。
- 跑滿一場道具淨變化 **A +7.4/場、B +2.9/場(淨增)** → 礦洞回補道具 > 消耗,囤貨還會漲,跑滿沒有耗竭風險。

### 真正的浪費 = 兩個 bug 讓真機每場只跑 ~15 動作(~1/3 窗口)
1. no_pit 在 floor7_open=True 但沒捲動時回 0 步 → 3 次中止(planner.py:661 / mining_service.py:545)。
2. drill/bomb OCR 誤讀成 0 → 黑名單道具 → 鏟子見底後無法續挖。

→ 修這兩個 bug 讓每場用滿 6min ≈ **~3x 礦石**,且道具淨增。RL / cost-model 只有 +4% 天花板,不值得。

