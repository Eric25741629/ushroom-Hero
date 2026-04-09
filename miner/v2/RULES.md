# Miner V2 規則說明

這份文件記錄 `miner/v2` 目前採用的規則模型。

目的有兩個：

- 讓 V2 planner 的規則可維護
- 讓後續開發與實際遊戲規則、預期策略保持一致

## 文件目的

這份文件應該負責：

- 明確記錄 V2 planner 的規則
- 區分哪些是已確認規則、哪些只是目前假設
- 作為後續實作與測試的檢查基準
- 幫助確認 planner 的行為是否真的符合預期策略

## 文件範圍

目前這份文件涵蓋：

- 盤面 label 與其語義
- 最上層策略分類
- `dig` / `bomb` / `drill` 的動作語義
- 目前規劃器的優先順序
- 已確認的可視區外效應假設
- 仍待確認的規則項目

這份文件不處理 runtime integration。

## 盤面 Label 語義

V2 planner 目前把盤面 label 視為：

- `empty`：可到達的空地
- `void`：在 V2 規則上等同 `empty`，視為同一種可到達空地
- `dug_pit`：已經挖完的 pit，等同空地
- `dirt`：可到達、成本為 1 的土塊
- `rock`：可到達、成本為 2 的石塊
- `one_hit_rock`：可到達、成本為 1 的石塊
- `unreachable_empty`：尚未曝光的空地，曝光後才會轉成可到達
- `unreachable_dirt`：尚未曝光的成本 1 方塊
- `unreachable_rock`：尚未曝光的成本 2 方塊
- `reachable_pit`：尚未收集、目前可到達的 pit
- `unreachable_pit`：尚未收集、目前還沒曝光或還不可到達的 pit

正規化規則：

- 舊 label `pit` 會先轉成 `reachable_pit`
- V2 核心規則只應該處理 `reachable_pit` 與 `unreachable_pit`
- 在 planner 內部，`void` 會被收斂成與 `empty` 相同的可到達空地語義

## Pit 規則

已確認規則：

- `dug_pit` 不再算剩餘 pit，後續視為空地
- 盤面上只要還有任一 `reachable_pit` 或 `unreachable_pit`，策略分類就屬於 `has_pit`
- 如果盤面上沒有任何剩餘 pit，策略分類才是 `no_pit`
- `pit` 的優先級高於開啟 `floor7`
- `unreachable_pit` 不能因為暫時不可到達就被忽略
- `pit` 不一定要一變成 reachable 就立刻挖
- 但在真正結束本次 mining 之前，所有 pit 都必須轉成 `dug_pit`

對 planner 的意義：

- `has_pit` 盤面允許為了路徑或道具價值暫時延後某些 pit
- 但任何成功完成的計畫都必須滿足 `remaining_pits == 0`
- planner 必須有離場防呆，避免在仍有 pit 時產生可離場結果

## `y=0` Pit 防呆

已確認規則：

- 如果還沒挖掉的 pit 已經出現在 `y=0`，也就是盤面最上排
- 這個 pit 的優先級要提高到高於道具機會
- 不能因為底部有更漂亮的 `bomb` / `drill` placement，就把 `y=0` pit 繼續往後拖

對 planner 的意義：

- `top-row pit` 是硬優先級事件
- 只要存在 `y=0` 的未挖 pit，action ordering 和 heuristic 都要優先導向處理它

## 曝光規則

planner 每做完一個動作，都要重算曝光狀態。

目前的曝光模型：

- 可到達的空地會向四周相鄰空地擴散
- 與可到達空地相鄰的 `unreachable_*` 會轉成 reachable 狀態
- 其中包含 `unreachable_pit -> reachable_pit`

## 成本模型

目前採用的動作成本：

- 挖 `dirt` 或 `one_hit_rock`：`1`
- 挖 `rock`：`2`
- 挖 `reachable_pit`：`1`
- `unreachable_pit` 不能直接挖，必須先經過曝光轉成 reachable
- 使用 `bomb`：`3`
- 使用 `drill`：`3`

挖掘後的狀態轉換：

- `reachable_pit -> dug_pit`
- 一般可挖方塊 -> `empty`

## 最上層策略分類

### `has_pit`

條件：

- 盤面上仍有任一 `reachable_pit` 或 `unreachable_pit`

目標：

- 最終收集所有 pit

完成條件：

- `remaining_pits == 0`

優先順序：

- 只要還有 pit，開啟 `floor7` 就只是次要目標
- planner 可以偏好更便宜的路徑，或為了道具價值先往下挖
- 但不能因為省成本而漏 pit
- 若有 `y=0` pit，必須先處理它

### `no_pit`

條件：

- 盤面上沒有任何剩餘 pit

目標：

- 用最低成本打開 `floor7`

完成條件：

- `floor7_open == true`

## 道具規則

### `bomb`

已確認規則：

- 成本是 `3`
- 作用範圍使用專案既有的 bomb footprint
- 爆炸可以影響可視盤面下方
- 爆炸效果不受可視畫面邊界限制

目前 V2 的規劃假設：

- 只要 bomb footprint 觸及可視底部以下，就視為真實有效收益
- 如果 footprint 打到可視底部以下，planner 可以把它視為已打開 `floor7`
- 因此靠近底邊的 bomb 放置點，可能會比同形狀但更高的位置更有價值

對策略的意義：

- planner 應該允許「先往下挖，再放 bomb」
- 即使一部分收益發生在可視區外，底邊 bomb placement 仍然可能是正確選擇
- 但若此時已有 `y=0` pit，仍應先處理 top-row pit

### `drill`

已確認規則：

- 成本是 `3`
- `drill` 只算可視區 footprint
- 目前 footprint 依照專案既有 mechanics，為可視縱列加上底列左右延伸

目前 V2 的規劃假設：

- `drill` 不計算可視區外收益
- `drill` 模擬時只移除可視盤面內的格子

對策略的意義：

- planner 應該允許「先往下挖，再放 drill」
- `drill` 和 `bomb` 不同，不能因為可視區外的未知格而額外加分
- 但若此時已有 `y=0` pit，仍應先處理 top-row pit

## 道具價值方向

已確認的策略方向：

- `1x1`、`2x2`、`3x3` 這類道具價值，會受到放置位置影響
- 為了讓道具效益最大化，可能需要先往下挖，再使用道具
- 道具使用必須是主搜尋的一部分，不是外掛評估器

目前 V2 的實作方向：

- 動作一起進搜索：`dig`、`use_bomb`、`use_drill`
- action ordering 目前偏好：
  - 優先處理 `y=0` pit
  - 減少剩餘 pit
  - 增加可到達深度
  - 提高更低位置的道具放置價值
  - 讓底邊的 bomb placement 能被選中

## `floor7` 規則

V2 目前的定義：

- 如果最後一列已有可到達空地，視為 `floor7` 已打開
- 如果 bomb 模擬結果命中可視底部以下，也可以視為 `floor7` 已打開

## 目前已明確寫進 V2 的假設

目前已經寫入 V2 的假設有：

1. `empty` 與 `void` 在 V2 規劃語義上等價
2. `dug_pit` 在後續規劃中就是空地
3. `bomb` 對可視底部以下的影響視為有效
4. `drill` 只計算可視區效果
5. 為了放大道具價值，可以先往下挖，之後再用道具
6. 舊的 `pit` 不應再作為 V2 核心語義中的獨立狀態
7. `y=0` pit 是高優先級防呆事件

## 仍待確認的項目

這些規則在下一輪擴充前，應該先由你確認：

1. `bomb` 往左右邊界炸出可視區外時，是否也應該推定有額外價值，還是暫時只看底部
2. 最上排 pit 和其他 pit 的優先級差距要多大，是否需要更明確的硬規則
3. 道具規劃是否要明確把 `1x1` / `2x2` / `3x3` cluster reward 寫成正式規則，而不是只看成本改善
4. 如果 bomb 在可視區外打開 `floor7`，但畫面上還沒看到明顯連通，是否已經算完成

## 建議檢查方式

你可以直接把這份文件的每個章節標成其中一種：

- 正確
- 要修改
- 缺規則

後續實作應該先把這份文件對齊，再繼續擴充 planner。
