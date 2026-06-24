# 萬神試煉Beta（rogue）自動化方式評估

> 2026-06-12 研究記錄。**討論用文件,未改任何腳本。**
> 協議欄位權威:`docs/protocol/ROGUE_PROTO_SCHEMA.json`。
> 現有腳本:`battle/weekly_trials.py::fight_test`(OCR 點擊,跑 7 場)。

## 1. 這是什麼

「萬神試煉Beta」(副本 tab → scrollDungeon cell「萬神試煉Beta」→ cocos `RogueView`)是
**roguelike** 玩法,協議是 **`rogue` 模組(module 76 / cmd 高位 0x4C)**。

與週副本「萬神試煉」(`ws_token/dungeon.py` 的 `type=23`)是**完全不同的兩套協議**,
不可共用 dungeon.py。使用者把兩者「視同一個」是追蹤分類,實作上要分開。

## 2. 沒有掃蕩(已確認)

rogue 模組 34 條 cmd 全列**無 sweep 類指令**。它是逐關連打的 roguelike,
官方就是要真打,沒有提供掃蕩。→ 這是「為什麼要想辦法優化點擊」的根本原因。

## 3. 協議主流程(live 驗證,5554 閃電 CDP 9230)

cmd 對照(c2s/s2c 同 id):

| cmd | 名稱 | 用途 |
|---|---|---|
| 0x4c01 | rogue_info | 主面板資訊 |
| 0x4c02 | rogue_main_enter | 進場(回入場 reward_list) |
| 0x4c03 | rogue_main_over | 結算離場(return_type) |
| 0x4c04 | rogue_main_combat | 開戰(server 回 seed:uint64 + 雙方 p_battle_role) |
| 0x4c05 | rogue_main_result | 回報戰果 {result:0勝/1敗, precent:剩HP%} |
| 0x4c07 | rogue_bag | 背包 |
| 0x4c09 | rogue_attr_up | 屬性 |
| 0x4c0a~12,0x4c23 | rogue_branch_* | 分支事件(選擇/商店/戰鬥) |
| 0x4c13~15 | rogue_truntable_* | 轉盤 |
| 0x4c16~19 | rogue_science_* | 神樹祝福(科技樹) |
| 0x4c1a | rogue_week_reward | 週積分獎勵 |
| 0x4c08/1b/21 | rogue_reports / report_collect | 神戰報告 |

一輪 live 閉環(這場勝利,server 接受 client 回報的 result 並發獎):
```
開始 → btnEnsure 確認窗 → main_enter(0x4c02 回入場獎勵)
→ 開始挑戰 → tx main_combat(0x4c04) → rx(seed + 雙方 role)
→ client 載入戰鬥場景、跑模擬 → client 自動 tx main_result(0x4c05)
→ rx(reward) → main_over(0x4c03) 結算離場
```

## 4. 現有腳本在做什麼

`battle/weekly_trials.py::fight_test(d)`:OCR(`img_tools.click_str_by_server`)+ 固定座標,
跑 7 場。每場:`開始`→`開始`→`確定`(進場)→ 點空白 →`開始挑戰`→ sleep7 →`跳過`→
點空白 → 退出 →`結束本局`→`確定`→ 收尾。最後 `buy_god_everyweek` + 領週積分。

痛點:OCR 不穩(辨識漏字/誤判 + OCR server 往返)、固定座標吃畫面縮放會飄。

## 5. 三種自動化方式評估

### 5.1 純 WS(不開瀏覽器)— 對 rogue 不可行(除非偽造)

戰鬥勝負是 **client 拿 `main_combat` 回的 seed + 角色,在本地戰鬥引擎模擬**算出來的。
要純 WS 送出**合法**的 `main_result`,只有兩條路:

1. **重現 client 戰鬥模擬**(同 seed+角色跑出同結果)— 邏輯埋在遊戲引擎,沒解析、成本極高、
   版本一改就失效。不划算。
2. **盲送 `result=0` 偽造勝利** — 這就是使用者已否決的「自己造封包偷跑」。server 端是否用
   seed 回放驗算 = **未驗證**(要驗只能拿「戰力會輸的關卡」純 WS 送 result=0 看 code=0 還是
   判敗/0x0201)。屬 anti-cheat 灰區,有判敗/封號風險。

> ⚠ 記錄:研究中我一度對純 WS 可行性下過「定論」(先說不可行、後又自我推翻說可行),
> 兩次都**未經 rogue 自身的受控實測**。正確狀態是:**未驗證**。不要把任一方向當定論。
> (使用者另告知:某條「地獄之門純 WS 已驗證可行」的記憶不可採信。)

**結論:rogue 純 WS 沒有乾淨路線。使用者也不想走偷跑。→ 這條放棄。**

### 5.2 H5 + cocos callback 取代 OCR — 可行且更穩(機制已驗證)

不換 WS、不偽造,只把「找按鈕的手段」從 OCR 換成**直接驅動 cocos 場景樹的按鈕 callback**:

```js
node._eventProcessor.bubblingTarget._callbackTable['click']
    .callbackInfos[].callback.call(target, btn.getComponent('cc.Button'))
```

(更正 2026-06-20:RogueView 按鈕**可正常點擊**(座標點擊 / `emit('click')` 皆有效)。
先前「`mouse.click`/`emit('click')` 都無效、必須走 callbackInfos」的記載**有誤,已移除**。
callbackInfos 直呼仍是可選的更穩做法,但**非必要**;有些畫面的按鈕走 `clickEvents`,要 case by case。)

**為什麼比 OCR 穩**:OCR 不穩來自「辨識」+「像素定位」兩層;callback 用場景樹 name/label
路徑定位、直接觸發 click handler,兩層都繞掉 → 不吃縮放、不怕辨識錯、不用截圖往返,快。

**戰鬥那步仍留給 client 真跑**(sleep + 跳過),callback 只取代導航點擊。

**適用範圍(重要,別過度推論)**:
- 萬神試煉 7 場是**同一組 UI** → 一套 callback 路徑跑 7 場 OK,這個 case 很適合。
- 「遊戲所有副本/任務」:機制通用,但**每種畫面的場景樹按鈕路徑要各別 recon**,不是寫一次到處跑。
- 限制:① 只限 H5(cocos)後端,需要 JS 注入;② 純前端動畫窗/「點空白關閉」可能無固定按鈕節點,
  那種還是要點座標/發 touch;③ 時序:按鈕還沒生成(載入/動畫中)時找不到節點,要等或重試
  (OCR 也有此問題)。

**性質**:這只是「更穩地真打」,**不改變遊戲邏輯、不繞過戰鬥、不掃蕩**。使用者理解正確。

### 5.3 ADB 改 WS — 基本不可行

- ADB 是原生 App,不是瀏覽器,**沒有 cocos JS 注入**能力,只能 uiautomator2 點座標/OCR。
- 想在 ADB 上發 WS,只能用撈到的 token **另開一條 WS**(ws_token 後端)。但那條跟 App 自己的
  WS 是**兩條獨立 session,同帳號會互踢** → 無法「App 前台真打戰鬥」同時「另一條 WS 發指令」。
  (互踢屬 ws_token 整合的普遍行為;同帳號並行的具體表現若要落地需實機確認。)
- 結論:ADB 沒有乾淨的半 WS 半 UI 路線。要嘛維持 OCR/座標,要嘛整台改純 ws_token(又回到
  5.1 戰鬥沒人算的問題)。

## 6. 資源 / 背景掛瀏覽器(使用者顧慮)

**資源直覺正確**:純 WS = 一條 socket + protobuf,幾乎不吃配備;H5 = Chrome 跑 WebGL canvas +
遊戲引擎 + 戰鬥動畫,吃 CPU/GPU/RAM,多台疊起來重。但 rogue 純 WS 做不到(§5.1),
所以「跑遊戲」省不掉 — **能省的只有「顯示」,不是「遊戲運算」**。

**背景/headless 掛瀏覽器**:
- `web_device.py` 已有啟動旋鈕:`headless`(預設 False)、`manual_launch_force_headful`、
  `args`(可注入 Chrome 參數);記憶亦載 headless live-view 在 VPS 跑過
  (`manual_launch_force_headful=false`)。→ **headless / 背景是專案本來就支援的方向。**
- ⚠ 風險(需實測,勿當定論):
  1. **WebGL/cocos 在 headless 下能否正常渲染並推進**:headless Chrome 的 WebGL 常走
     SwiftShader 軟體渲染,可能黑屏或反而更吃 CPU(省顯示但不省、甚至多耗運算)。
  2. **requestAnimationFrame throttle**:背景/最小化/headless 分頁的 rAF 會被降頻甚至暫停。
     rogue 的戰鬥計時、「跳過/結束本局」常綁動畫完成 callback → 降頻可能**卡住流程**。
  - 緩解方向(待驗):Chrome 啟動參數 `--disable-background-timer-throttling`
    `--disable-renderer-backgrounding` `--disable-backgrounding-occluded-windows`
    讓視窗即使不在前景也全速;或用 `--headless=new` 並實測 cocos 是否正常。
- cocos 邏輯層(場景樹、callback、WS 收送)跑在 JS 引擎,**理論上不需畫面真的渲染**就能推進,
  所以 callback 驅動 + headless **有機會**成立,但 rAF/動畫完成事件是最大的未知,**必須實測一場**。

## 7. 待辦 / 待驗(不改腳本,先討論定案)

- [ ] (待定案)是否走 §5.2 H5 cocos-callback 取代 OCR 改寫 `fight_test`(戰鬥仍 client 真跑)。
- [ ] (實測,不改正式腳本)headless / 背景全速參數下,rogue 跑完一場 callback 流程是否正常
      (重點看 rAF throttle 會不會卡「跳過/結束本局」)。用一次性探測腳本驗,數據說話。
- [ ] (可選,anti-cheat 灰區,使用者決定)受控測「會輸的關卡純 WS 送 result=0」→ code=0 還是判敗,
      才能定案 §5.1 純 WS 到底可不可行。預設**不做**(使用者不走偷跑)。

## 8. 已記錄的硬事實(供日後引用)

- rogue = module 76 / 0x4C,roguelike,**無掃蕩**。
- 主流程 enter→combat(seed)→client 模擬→result→over;result c2s 只有 {result, precent},
  **無 operators 回放序列**。
- RogueView 按鈕**可正常點擊**(座標 / emit('click') 皆可);callbackInfos 直呼為可選的更穩做法，非必要。（更正 2026-06-20，原記「無效」有誤）
- 純 WS 可行性 **未驗證**(別當定論,任一方向都是)。
- ADB 無 JS 注入;另開 token WS 與 App 同帳號互踢。
- headless/背景:`web_device.py` 支援;最大未知是 WebGL 渲染 + rAF throttle,需實測。
