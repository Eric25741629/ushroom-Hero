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

## 9. 2026-06-29 live recon(5554,週一 rogue 重置,CDP 9230,全程 read-only)

用 cocos 場景樹 + WS ring-buffer hook 把整套 flow 與協議欄位重抓一遍。與 06-12 文件一致,並補上欄位細節與一個重大導航發現。

### 9.1 完整 UI flow(節點路徑 + 對應 WS)
canvas DOM = 540x960,**Cocos 設計解析度 = 720x1280**(`cc.view.getVisibleSize()`),縮放 0.75。
worldPosition(design,原點左下)→ css tap:`x*0.75, (1280-y)*0.75`。

進場(冷啟,本帳號本週重置後為冷啟「開始」非「繼續」):
1. 主頁底部 tab `MainView/tab/scrollTab/view/content/3`(副本)→ 副本列表(rogue 在 `萬神試煉Beta` 卡,列表卡**無次數顯示**)
2. 點該卡入場 → `RogueView`(主面板),按鈕:`RogueView/view/btnStart`(開始/繼續)、`btnRecord`(神戰報告)、`btnScience`(神樹祝福)、`btnStore`(秘寶閣)、`btnReward/score`=週積分、`btnReward/num`=獎勵里程碑
3. `btnStart` → `RogueEnterView`(開局設定,`bg/txtLev`=第N關、`bg/btn`=開始、`bg/btnClose`=✕、`bg/btnLast`=往前選起點)
4. `RogueEnterView/bg/btn`(開始)→ **確認窗「是否確認開啟新一局試煉」** → `確定`
5. → `tx rogue_main_enter(0x4c02){1:1}` → `RogueMainView`(關卡視圖)+ `RogueGoodsGetView`(開局獎勵窗,點空白關)

關卡循環(勝利):
6. `RogueMainView/view/btnStart`(開始挑戰)→ `tx combat(0x4c04){}` → `rx{1:code,2:seed,3:atk_role,4:def_role}` → client 本地模擬 → `tx result(0x4c05){1:result,2:precent}`(**client 回報**)→ `rx attr_up(0x4c09)+bag(0x4c07)+result s2c{2:is_win}+rogue_info` → `RogueBattleResultView`(勝利/對決窗)
7. 點空白關結果窗 → 回 `RogueMainView`,**關卡 +1**,重送 `enter(0x4c02){1:<新關>}` →(回 6 續打)

退出/結算:
8. `RogueMainView/view/btnClose`(右下紅箭頭)→ `RogueEndTipsView`(`desc`=選擇暫時離開或結束本局、`btn1`=結束本局、`btn2`=暫時離開、`btn3`=取消)
9. `btn1`(結束本局)→ **確認窗「是否確認結算本局」** → `確定`
10. → `tx over(0x4c03){1:0}` → `RogueRecordInfoView`(本局報告)+ `GoodsGetView`(獎勵入帳,主帳號 attr_up)。run **bank** 在當前關,下次進場 RogueEnterView 顯示該關(「基於上一局試煉終點」)。

### 9.2 ⚠ 重大導航發現(可能是「假完成」/ 點不動的根因)
rogue 的兩個確認窗 **「是否確認開啟新一局試煉」「是否確認結算本局」掛在 `TopView/MessageView`(通用訊息框),不在 `NormalView`**。
→ 任何「只看 NormalView active overlay」的狀態判斷都看不到它們;`MessageView` 是節點池,殘留字串不可信,要靠截圖/OCR 或讀**當前可見**節點判斷。
→ 對 bot:`_advance_to_stage` 輪點「開始」後若沒處理這個 TopView 確認窗,就會卡在 RogueEnterView(OCR 點「開始」回 True 但畫面不動,實測重現)。確認窗按鈕走 `MessageView` 內的「確定/取消」。

### 9.3 資源/狀態語意(本局報告 RogueRecordInfoView 權威)
- **「試煉之心」= ❤(左上紅心計數)= 局內生命**;報告欄「剩餘試煉之心」。使用者先前說的「13❤」就是這個。實測:**勝一關 ❤ 不變(16→16)**,推測敗北才扣(自然失敗未驗,見 9.5)。
- **古銀幣 = 左上金幣計數**(176→182,勝利 +6);報告欄「本局獲得古銀幣總數」。
- 左上第三個計數(53)= 某試煉道具/卷軸。
- `RogueView` 主面板「0/2500」= 週積分進度;「0/10」= **獎勵里程碑檔位數(非次數)**。
- 列表卡與主面板**都沒有顯示「剩餘挑戰次數」**;未發現硬性每日/每週開局次數上限欄位。

### 9.4 WS 欄位細節(本次實抓,補 ROGUE_PROTO_SCHEMA.json)
- `rogue_info(0x4c01)` s2c:`{1:當前關卡(實=36→勝後37), 2:古銀幣, 3:週結算時間戳(1783267200), 7:[{id,值} 大清單=主帳號資源鏡像]}`。
- `rogue_main_enter(0x4c02)` c2s `{1:return_type}`;s2c `{1:{1:關卡, 2:{角色/技能/裝備"梅利號"...}, 3:[敵人/關卡陣列]}}`。
- `rogue_main_combat(0x4c04)` c2s `{}`;s2c `{1:code, 2:seed(uint), 3:atk_role, 4:def_role}`(本帳號鏡像戰:atk/def name 皆=自己「下不維力炸醬麵」)。
- `rogue_main_result(0x4c05)` c2s `{1:result(0勝/1敗), 2:precent}`;s2c `{1:code, 2:is_win, 3:precent, 4:run_state, 5:{...}}`。
- `rogue_main_over(0x4c03)` c2s `{1:return_type=0}`;s2c `{1:{1:積分?,2:抵達關卡,...,7:剩餘試煉之心,8:古銀幣,12:[bag]}, 2:{run reward}}`。
- 其他出現:`rogue_bag(0x4c07)`、`rogue_science(0x4c16,神樹各節點等級)`、`rogue_attr_up(0x4c09)`、`0x4c20`(redpoint?)、`rogue_red(0x4c1e)`。

### 9.5 自然敗北行為(2026-06-29 grind 實證,5554)
從第37關開新局自動連打:第1-4關勝(關卡 37→41),**第5關(宗師-05,第40關)敗北**。實證:
- **敗北結果窗 = 同一個 `RogueBattleResultView`,banner 變藍色「失敗」(勝為金色「勝利」)**,顯示「對決 X vs 宗師-05」+ **「失去 ❤1」**。判定來源:`result(0x4c05)` s2c `{2:is_win}`(1勝/0敗,client 開打即算好送出,動畫稍後才播完顯示窗;`is_win` 是最可靠的勝負信號,別靠 OCR「勝利/失敗」字)。
- **敗北 = 扣 1 試煉之心(❤),停在同一關重試**(不進關)。點空白關失敗窗 → 回 `RogueMainView` 同關,重送 `enter(0x4c02)`,`開始挑戰` 仍在。背包 ❤ 16→…→14(每敗 -1)。
- **run 不因單次敗北結束**;**試煉之心(❤)歸 0 才真正結束一局**。banked = 最高抵達關。
- ⇒ **「假完成」bug 根因確證**:`battle/weekly_trials.py::_battle_loop` 偵測到「失敗」就 `break` 是錯的 — 敗北是 run 內正常事件(扣心重試)。正確停止信號是 **❤(試煉之心)=0 → 該局結束**,或主動「結束本局」。
- 戰鬥動畫播放時畫面在 **BattleView 層**(「與對手激烈搏鬥！正在挑戰…/逃跑」),此時 `NormalView` overlay=空 → 別把「空 overlay」誤判為離開 rogue。

### 9.6 「8 局」語意(使用者 2026-06-29 定案)
**「8 局」= 使用者選定的每週開局(局)目標,非遊戲硬限**(協議無對應硬欄位)。一局 = 開始→爬到 ❤ 歸 0 自然結束(或主動結束本局)→結算。跑滿 8 局才寫週記錄。
→ 重寫 `fight_test` 方向:把 enter→(開始挑戰 loop 到 ❤=0 / run 結束)→結束本局→重進 包成 **8 次迴圈**;每局內**敗北不 break,續打到 ❤=0**;單局/單關仍保留時間上限防呆。

### 9.7 recon 工具
scratchpad `rogue_recon.py`(attach CDP 9230 + WS ring hook + 場景樹 dump/find + emit-click/clickEvents + 座標 tap + protobuf walker)。
點擊可靠度:`emit('click')` 對有 'click' listener 的鈕穩(btnStart/btnClose/各 view btnClose);**確認窗(MessageView)的確定/取消用座標 tap**(emit 不一定到);RogueEndTipsView 結束本局用座標。
