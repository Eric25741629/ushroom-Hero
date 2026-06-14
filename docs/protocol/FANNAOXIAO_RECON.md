# 煩惱消 (act_type 224) — Minigame Recon

Recon device: `emulator-5556` (菜雞), live CDP `http://127.0.0.1:9223`.
Date: 2026-06-14. Method: read-only Cocos scene-walk + `netManager._protoClass`
inspection + opcode-map / game-JS static read (`auth_state/mobile_recovery_emulator-5554/assets/assets/script/index.js`)
+ live config-table dump via Playwright. No game state mutated; no round played
(activity window is CLOSED — see §1, nothing to enter).

> ## ⚠ LIVE UPDATE 2026-06-14 — the §1–§4 "2048" verdict below is SUPERSEDED.
> When the event actually opened on `7fe98fc6` (小寶, CDP 9226), the live game
> view is **NOT 2048**. It is a **左右消除 (left-right crush)** block game.
> The "2048" guess in §1–§4 was inferred from an OLD season skin in the
> static JS; the LIVE season skin `ActivityNewYear24Match*` is a different
> engine. The protocol family (act_clear_game, cmds 6464/6465,
> CLIENT-AUTHORITATIVE) is correct; the GAME MODEL and the move primitive are
> different. **Read §7 (LIVE-CONFIRMED) for the real mechanics, node paths,
> move sequence, and entry-cost finding.** The solver/driver are built to §7.

## TL;DR / Verdict

| Question | Answer |
|---|---|
| Is 煩惱消 ACTIVE on this account now? | **NO.** Banner `icon_224` exists but `active:false`; the `act_clear_game` proto is NOT lazy-loaded (245 protos, no `clear_game` key). Activity in a closed window. |
| Entry location | Right-side activity banner `icon_224` under `/UIRoot/NormalView/MainView/top/systemTop/btnRightRoot/icon_224` (child `name` Label = `煩惱消`). |
| act_type value | **224** (from banner node name `icon_224` + `configActivity_control` rows `2240001/2/3` with act_type col 224 + `configActivity_term` row `[224, 40, ...]`). High confidence. |
| What IS the game? | **A 2048 game** (4×4 swipe-merge), NOT a 三消/match-3. Tile icons `wsj3_2048_qz01..10`; tile values `2,4,8,16,32,64,128,256,512,1024`. The "美味大作戰" screenshots (`minigame_analysis.png`) are the food-themed skin of the SAME engine. |
| Protocol family | **ACT2, module 25** — `act2.act_clear_game_*`. **cmd ids LIVE-CONFIRMED from the client opcode map** (not guessed). |
| `act_clear_game_info` cmd | **6464** (`= 25*256 + 64`). c2s `{act_type}` → s2c `{act_type, save:string, score:uint32}`. Read-only query. |
| `act_clear_game_save` cmd | **6465** (`= 25*256 + 65`). c2s `{act_type, save:string}` → s2c ack. **This is a client→server STATE UPLOAD of the whole board.** |
| Server-authoritative or client-validated? | **CLIENT-AUTHORITATIVE.** Board, RNG spawn, merge rules, and score are ALL computed in JS; the server only persists the `save` JSON blob and the top `score`. Per project rule → drive the real client, do NOT forge `save`. |
| Implemented `ws_token/fannaoxiao.py`? | **NO.** It would be a cheat (forging the score blob) AND can't run while the activity is closed. Recommended path = Playwright client drive (§4). |

## 1. Is it active? Where is the entry?

Scene-tree keyword scan (`煩惱/消/merge/clear/美味/大作戰/2048/合成/icon_224`) on CDP 9223:

```
/UIRoot/NormalView/MainView/top/systemTop/btnRightRoot/icon_224          active=false
/UIRoot/NormalView/MainView/top/systemTop/btnRightRoot/icon_224/name     label="煩惱消" (active=true, but parent hidden)
```

The `btnRightRoot` activity-banner row currently shows ACTIVE banners:
`btnRechargeGift (儲值好禮)`, `btnRankingRush (衝榜活動)`, `btnKungfuRace (菇菇武道會)`,
`btnH5Gift (NT$1超值禮包)`. The event icons `icon_3 (菇菇機)`, `icon_224 (煩惱消)`,
`icon_302 (返利寶箱)`, `icon_515 (星光大賞)`, `icon_4003 (傳奇大亨)` are all
`active:false` — none of those events is open right now.

Corroborating dormancy evidence:
- `netManager._protoClass` has **245** entries and contains NO `act_clear_game_*`
  key. The ACT2 minigame protos load only when the player opens the activity panel.
- `cc.director.getScene()` = `launch` (cocos 3.6.x), WS cnet ready, no module-25 traffic.

`configActivity_control` / `configActivity_term` carry the static definition for
act_type 224 (so the activity is configured, just not in its open window):

```
configActivity_control rows (act_type col == 224):
  [2240001, 700070633, 224, 0,0,0,0, 220, 220, 1]
  [2240002, 700070633, 224, 0,0,0,0, 224, 224, 1]
  [2240003, 700070633, 224, 0,0,0,0, 224, 224, 1]
configActivity_term rows referencing 224:
  [224,     40,  [400003,400003], 301130,    3, null,      0, null, [1038]]   # parent term, type=40
  [2240001, 224, [2240001,2240001], 700070633, 1, [2240001], 1112, null, null]
  [2240002, 224, [2240002,2240002], 700070633, 1, [2240002], 1112, null, null]
  [2240003, 224, [2240003,2240003], 700070633, 1, [2240002], 1112, null, null]
```
=> 3 sub-stages `2240001..2240003`; UI/lang id `700070633`; the `1112` column is
likely a cost/rank ref; `ActivityType` for this term is **40**. (Column semantics
are best-effort.)

## 2. Game rules (read from the client JS, definitive)

Source: `ActivityHalloween25GameView` / `ActivityNewYear24MatchGameView` in
`auth_state/.../script/index.js`. These reuse the **shared `act_clear_game` engine**
that 煩惱消 (act_type 224) also uses (same `configMerge_card`, same cmds 6464/6465,
same `MatchGameUpdateInfo` event). The 煩惱消 season-specific view chunk isn't loaded
while closed, but the engine is identical.

- **Board**: 4×4 grid. Cell id = `100*y + x` where `x` = column 1..4, `y` = row 1..4.
  Tile state held in `this._items[100*y+x]` (a sparse array; empty = null).
- **Tiles** (`configMerge_card`, 10 rows): `id` 1..10, `num` =
  `2,4,8,16,32,64,128,256,512,1024`, `merge_id` = id+1 (id=10 has `merge_id:0` ⇒
  the cap `C`, the max tile that can no longer merge). Icons `wsj3_2048_qz01..10`.
  The label drawn on a tile is `o.id.toString()` (1..10), i.e. tiles show the
  rank index, not the `num`.
- **Move**: swipe gesture on `content/nodeTouch`. `onTouchStart` records pos;
  `onTouchEnd` computes delta — if `abs(dx)<50 && abs(dy)<50` it's ignored;
  otherwise the dominant axis picks `LEFT/RIGHT` (x) or `UP/DOWN` (y).
- **Merge logic** (`move(dir)`): classic 2048. Iterate cells in the direction's
  scan order; each tile slides until it hits a wall or another tile. If the blocker
  has the SAME `value`, `value < C`, and hasn't merged this turn (`mergedFrom.length==0`),
  the two combine into `value+1` at the blocker's cell. Otherwise the tile stops
  adjacent.
- **Spawn**: after any move that changed the board, `addRandomNumber()` places a new
  tile in a random empty cell — value `1` with 90% prob, value `2` with 10%
  (`Math.random()<.9?1:2`). (Client-side RNG.)
- **Score**: each merge into value `t` adds `2 * configMerge_card.getDataByKey(t).num`
  (e.g. merge two "16" tiles → new "32", score += `2*32 = 64`). Shown in
  `content/nodeTop/txtScore`; best in `txtBestScore`.
- **Game over** (`isGameOver()`): no empty cell AND no orthogonally-adjacent equal
  pair with `value < C`. On game over → `onGameOver()` reports the result (see §3).
- **Goal**: maximize **score** (and `curMaxNum` = highest tile reached). Rewards/
  rank are score-based; there is no fixed clear-target or move limit in the engine.

## 3. Protocol (LIVE cmd ids from the client opcode map)

From `index.js` opcode maps (c2s `name→id`, s2c `id→name`):

```
act2.act_clear_game_info_c2s = 6464   ( = 25*256 + 64 )
act2.act_clear_game_info_s2c = 6464
act2.act_clear_game_save_c2s = 6465   ( = 25*256 + 65 )
act2.act_clear_game_save_s2c = 6465
```

Message bodies (from `ACT2_PROTO_SCHEMA.json` + client handlers):

```
act_clear_game_info_c2s   { 1:act_type uint32 }
act_clear_game_info_s2c   { 1:act_type uint32, 2:save string, 3:score uint32 }
act_clear_game_save_c2s   { 1:act_type uint32, 2:save string }
act_clear_game_save_s2c   { 1:act_type uint32, ... ack }
```

Client handlers (verbatim shape):
```js
send_act_clear_game_info_c2s(a){ netManager.send("act2.act_clear_game_info_c2s",{act_type:a}) }
on_act_clear_game_info_s2c(a){ DataCache.gameSaveProgress=a.save; DataCache.gameTopScore=a.score;
                               normalEvent.emit(MatchGameUpdateInfo) }
send_act_clear_game_save_c2s(a,e){ netManager.send("act2.act_clear_game_save_c2s",{act_type:a, save:e}) }
```

**The `save` string** is built every move by `saveGameInfo()` and is the full board:
```js
// posInfo = { <cellId 100*y+x> : <tileValue 1..10> }  for every occupied cell
// mergedList = [ {k:<tileValue>, v:<#merges this move>}, ... ]
const s = { curScore: this.curScore, posInfo: e, mergedList: i };
send_act_clear_game_save_c2s(act_type, JSON.stringify(s));
```

**Resume on entry**: `act_clear_game_info_s2c` returns the last `save` blob + best
`score`. The client `JSON.parse(save)` and rebuilds the board from `posInfo` (or
starts fresh with two random tiles if no save / new game).

**Game-over report** is NOT in the clear_game family — it goes through the generic
activity result cmd:
```js
onGameOver(){
  let t=[{k:0, v:this.curScore, s:""}, {k:1, v:this.curMaxNum, s:""}];
  send_24_101(act_type, 1, 1, t);   // act.* family; server then emits OnActivityGameResult → ResultView
}
```

## 4. Verdict: CLIENT-AUTHORITATIVE → drive the real client

Every gameplay decision is in JS:
- board layout, slide/merge resolution, the cap `C`, win/lose detection — local;
- the **random spawn** (1 vs 2, and which empty cell) — `Math.random()` client-side;
- the **score** — accumulated client-side;
- the server's only role is to **store the `save` JSON blob and the top `score`**;
  `info_s2c` just echoes them back. There is no server validation of the board's
  legality — `save_c2s` is a trusted state upload.

Per the project rule ("結果由客戶端本地計算再回報伺服器的 → 不偽造 WS，改用 Playwright 驅動
真實客戶端"), this is exactly the forbidden-to-forge case. A pure-WS bot COULD
trivially cheat by sending a fabricated high-score `save` blob, but that is
out-of-policy (and bannable). **Automate by driving the real client.**

### Recommended implementation (Playwright + cocos, no WS forging)

Because the board is fully in cocos node state, read it directly (no CNN needed):

1. **Entry / gate**: on main page, find `btnRightRoot/icon_224`; only proceed if
   `icon_224.active === true` (activity open). Then `icon_224.emit('click', icon_224)`
   to open the activity main view; from there click the play/start button to open
   the `Activity<Season>GameView`. (When the event is live, scene-walk the opened
   view to record the exact start-button path, mirroring the TYCOON recon recipe.)
2. **Read board from cocos** (no screenshot OCR):
   - The game view stores tiles in the component instance (`this._items`), but that
     instance isn't trivially reachable by name. The robust read is the child nodes
     under `content/nodeMap`: each live tile node is named `(100*y+x).toString()`
     and its `icon/num` Label holds the tile rank (1..10). Walk `nodeMap.children`,
     parse `name` → (x,y), parse the `num` label → value. Empty cells = absent.
   - Alternatively, read the last uploaded `save` via `act_clear_game_info_c2s`
     (cmd 6464, read-only) and `JSON.parse(save).posInfo` — this is a legitimate
     READ and gives the exact `{cellId:value}` map without scraping nodes.
3. **Compute the move**: standard 2048 solver (expectimax depth-2/3, or a simple
   corner+monotonicity heuristic). State is tiny (16 cells, values 1..10).
4. **Issue the move**: the input is a SWIPE on `content/nodeTouch`, not a button.
   Two options:
   - **Synthetic touch via cocos**: emit `cc.Node.EventType.TOUCH_START` then
     `TOUCH_END` on `nodeTouch` with UILocation deltas > 50px in the chosen axis
     (the handler only reads `getUILocation()` start/end). This bypasses screen
     coords.
   - **Playwright drag** on the canvas: `page.mouse.move/down/move/up` across the
     board region (CDP viewport must be set first; see the H5 540×960 viewport note).
     Coarser but engine-agnostic.
   After each move, wait for `isMoving` to clear (merge/slide tween ≈ 0.2s) before
   the next move; re-read board; repeat until `isGameComplete` (game over) or a
   target score / max tile is reached.
5. **Bounded run**: stop at game over (engine fires `onGameOver` → result view).
   No currency spend in core play (entry may cost an item — check `configActivity_term`
   tail `[1038]` for the parent term before any paid retries; default to a single
   free run).

### Why pure-WS auto is rejected (and what a "safe WS" reduction would be)
- Forging `act_clear_game_save_c2s` with a hand-built high score = cheating. Out of scope.
- The only legitimate WS use is the **read** `act_clear_game_info_c2s` (6464) to fetch
  the resume blob / best score for state, plus reading `send_24_101`/result via
  the live client. Movement itself must come from the real client.

## 5. Discovery method + hooks for when it activates

When the banner `icon_224` goes `active:true` (event opens), on CDP 9223:

1. Re-run the scene scan; `netManager._protoClass` should now contain
   `act2.act_clear_game_info_c2s` once the activity panel is opened (or call it via
   `utils/web_game_api.WebGameAPI.call_raw(6464, <{act_type:224}>)` after the proto
   loads — note `call_raw` sends raw bytes, so build the body `{1:224}` =
   `08 e0 01`).
2. Install the idempotent WS ring hook (`tools/_recon_inventories.py` template or the
   skill's `attach_and_probe.py`), then drive the entry + ONE move and drain:
   - `icon_224.emit('click', icon_224)` → wait 2.5s → screenshot → record the
     activity-main and start-button node paths.
   - open the game view, do one synthetic swipe, drain → confirm the only frames are
     `6465` (save upload) tx + `6464` info; confirm NO server move/validate cmd.
3. Capture the live `Activity<Season>GameView` node tree to lock the start-button
   path and the `content/nodeMap` / `content/nodeTouch` paths for the season skin.
4. The act_type to send is **224** (already known from config + banner name).

## 6. Why no live round was played / no module created
- Activity is CLOSED → `icon_224.active:false`, proto not loaded, no game view to
  enter. There is nothing to play; the "one bounded round" allowance is moot.
- `emulator-5556` is bot-owned and was probed READ-ONLY only.
- No `ws_token/fannaoxiao.py` created: a pure-WS implementation would have to forge
  the client-authoritative `save` blob (cheating, out-of-policy). The correct
  implementation is a Playwright client driver (§4), to be built when the event is
  live so the season view paths and the synthetic-swipe input can be verified.

## Follow-up (when 煩惱消 is open)
1. Capture the `Activity<Season>GameView` scene path + start-button path (one click).
2. Verify the synthetic TOUCH_START/TOUCH_END swipe on `content/nodeTouch` actually
   triggers `move()` (vs. needing a Playwright canvas drag).
3. Confirm board read via `act_clear_game_info_c2s` (6464) `save.posInfo` matches the
   on-screen `nodeMap` children (cross-check the two read paths).
4. Confirm whether entry/retry costs an item (term tail `[1038]`); cap to free runs.
5. Decide a stop target (e.g. play to game-over once/day for the rank reward) and wire
   a Playwright task under the dual-backend pattern (H5-first).

---

## 7. LIVE-CONFIRMED (2026-06-14, device `7fe98fc6` 小寶, CDP 9226) — AUTHORITATIVE

When the event opened, the live game was driven end-to-end (entered, board read,
moves issued, ~24 bounded moves played, score 1→15, level 1→2). Everything below
is verified on the running client, not inferred.

### 7.1 The game is 左右消除 (left-right crush), NOT 2048

Live season skin view = `ActivityNewYear24MatchGameView` (class
`ActivityNewyear24MatchGameView` in `index.js`). Config table
`configLeft_right_crush` (level table) + `configLeft_right_crush_block` (blocks).

- **Board** = a vertical stack of ROWS over **9 columns** (`y=9`). The BOTTOM row
  is `RowList[RowList.length-1]`; new rows spawn at the bottom and push the stack
  UP. **Game over when `RowList.length >= R` where `R=12`** (`banDefeat` is set,
  so the bot only needs to keep clearing; defeat just ends the run).
- **Block** = a contiguous span `[pos, pos+length)` with a colour class 1..6.
  `index` → (colour,width): `width=(index-1)//6+1`, `colour=(index-1)%6+1`.
  (index 1..6 = w1, 7..12 = w2, 13..18 = w3, 19..24 = w4.) Geometry: column
  width `T=66`, row height `D=60` (nodeMap anchored (0,0)).
- **MOVE = drag ONE block horizontally inside its own row** to a target column.
  The block may only slide through EMPTY cells (cannot jump a blocker;
  `getGridMoveRange` / `moveGrid`). There is NO directional swipe / no 2048 merge.
- **Clear**: after every move the board SETTLES — blocks fall straight down from
  upper rows into empty cells of the row directly below (`checkGridDrop`), then
  any FULLY-filled row clears + scores while fully-empty rows are removed and the
  stack shifts down (`checkCompleteRow`). Repeats until stable.
- **Score**: `addScore` adds `configLeft_right_crush[level].score` per cleared row;
  level rises when score crosses `id[1]` thresholds (lvl1 0–10, lvl2 11–30, …).
- **Optional power-ups** (NOT used by the bot): `content/nodeBtn/btnColor`
  (destroy all of the most-common colour, consumes `left_right_crush_color_item`)
  and `content/nodeBtn/btnHammer` (split a length>2 block into unit blocks,
  consumes `left_right_crush_trans_item`).

### 7.2 BOARD READ (live-confirmed, two equivalent paths)

**Path A — cocos view instance (used by the driver; no OCR):**
```js
const v = uiMgr.getView("ActivityNewYear24MatchGameView");
// v.RowList[i].gridList[j] -> {pos, length, color, index}   (rows[0] = TOP)
// v.curScore, v.curLevel, v.canOperate, v.isWaitingTween, v.isTweening, v.isGameComplete
```
**Path B — legit WS read `act_clear_game_info` (cmd 6464 = `0x1940`):**
body `{1:act_type}` (224 = `08 e0 01`) → s2c `{1:224, 2:save string, 3:topScore}`.
The `save` is a SPACE-separated string (NOT JSON):
```
"<score> <adN1> <adN2> <buyN1> <buyN2> <useN1> <useN2> <row0> <row1> … <rowN> <nextRow>"
```
each `<rowK>` token = `pos_index+pos_index+…` (or `0`), listed BOTTOM-row-first;
the FINAL token is the next-row spawn info. Live example:
`"15 0 0 0 0 0 0 5_9+0_14 3_13+0_9 2_14+1_1+6_13 2_3+0_7+5_13 2_13+5_4+1_2"`.
Cross-checked: Path A and Path B parse to the IDENTICAL board (score 15, 4 rows).
`game_actions/fannaoxiao_solver.parse_save_string` handles Path B (reverses to
top-first to match RowList).

### 7.3 MOVE (live-confirmed) — Playwright mouse drag, NOT synthetic cc.Event

A synthetic `cc.Event.EventTouch` on `nodeTouch` did **NOT** fire `move()`
(`getUILocation()` did not resolve from a hand-built `cc.Touch`). The reliable
primitive is a **real Playwright mouse drag** on the canvas:

1. Map a cell centre (rowFromBottom 1-based, col 0-based) to world space:
   `nx=(col+0.5)*T`, `ny=(rowFromBottom-0.5)*D`,
   `w = v.utMap.convertToWorldSpaceAR(cc.Vec3(nx,ny,0))`.
2. World→screen px (design res from `cc.view.getVisibleSize()` = **720×1280**,
   canvas CSS rect = **540×960**; world Y is bottom-up):
   `sx = rect.left + (w.x/vis.w)*rect.w`, `sy = rect.top + (1 - w.y/vis.h)*rect.h`.
3. `page.mouse.move(sf) → down → move(mid, sf.y) → move(st.x, sf.y) → up`
   (horizontal drag; the engine reads dominant-x; >T px crosses a column).
4. Re-read the board; wait for `canOperate && !isWaitingTween && isTweening==0`
   before the next move.

`row_from_bottom = rowCount - row_index` (row_index is top-first RowList order).
Confirmed: a single drag relocated a block, triggered settle/drop/clear, raised
the score, and toggled `isWaitingTween`.

### 7.4 ENTRY + entry-cost finding

- Banner `icon_224` at `/UIRoot/NormalView/MainView/top/systemTop/btnRightRoot/icon_224`
  was **active** (live; child `time` label counted down ~20h; `RedPoint` on).
  The banner rail was already expanded (`btnRightRoot` active). `emit('click')`
  on it opens `ActivityNewYear24MatchView` (歷史最高分 / 排行 / 開始遊戲).
- Start a NEW game: emit-click `content/btnStart` →
  `send_24_100(NewYear24Game, 1)` → opens `ActivityNewYear24MatchGameView`.
  (`btnContinue` → `send_24_100(…,2)` resumes a saved board; `btnRestart`
  submits the old score via `send_24_101` then starts fresh.)
- **Entry cost = FREE.** Starting the game sent `0x1864` (`send_24_100`) with NO
  `0x0402` inventory-consume push. The `configActivity_term` 224 tail `[1038]` is
  a rank-reward pool reference, not a per-play cost. Only the optional
  btnColor/btnHammer power-ups cost goods — the bot never uses them, so the whole
  run (start + unlimited drags to game over) costs nothing.
- **Game-over / score report**: `gameEnd` → `send_24_101(NewYear24Game,1,1,[{k:0,v:curScore}])`
  → server emits `OnActivityGameResult` → `ActivityNewYear24MatchGameResultView`.
  The per-move board upload is `act_clear_game_save_c2s` (cmd 6465 = `0x1941`)
  built by `syncMapInfo()` — this is the REAL client's own upload (the bot does
  NOT forge it; per project rule it just drives the client which uploads naturally).

### 7.5 Implementation built to this recon

- `game_actions/fannaoxiao_solver.py` — pure/deterministic: `Board`/`Block`,
  `legal_moves`, `apply_move` (slide + settle drop + clear), `is_game_over`,
  `choose_move` (1-ply lookahead: prefer row-clears, else maximise a packing
  heuristic = low row-count + quadratic row-fullness), `parse_save_string`.
- `game_actions/fannaoxiao_driver.py` — live read (`read_board`/`read_state`),
  `issue_move` (Playwright drag w/ the §7.3 mapping), `enter_game`,
  `play_bounded(page, max_moves)` (read→solve→move loop with stuck/game-over/
  not-operable guards). JS for read + cell-screen mapping is embedded for reuse.
- `tests/test_fannaoxiao_solver.py` — 16 tests, all pass.

### 7.6 Bounded live-run result + task goal
**Task goal (user, 2026-06-14):** participate in 10 rounds AND reach **100 points
in ONE game**; the other 9 rounds only need participation (play casually).

Live run on `7fe98fc6`: entered the game, played ~79 bounded moves total. With the
1-spawn-ahead (green-bar preview) solver, score climbed **15 → 124** (level 2 → 5)
while the stack stayed at 3–7 rows (well below the 12-row game-over line). **The
100-point single-game goal is met/exceeded (124).** Read→solve→move loop confirmed
on the real game; **zero goods spent** (no btnColor/btnHammer).

### 7.6a The green-bar PREVIEW makes the next spawn KNOWN (not RNG)
The green bar at the bottom of the game (`content/nodeNextRow`) shows the NEXT row
that will spawn. It is `v.nextRowGridInfo` = `[[pos, index], …]` (live-confirmed:
matches the on-screen preview and is the exact row `createRow` spawns next). The
solver therefore does deterministic ONE-SPAWN-AHEAD lookahead: for each candidate
move it simulates the move + settle, THEN spawns the previewed row + settle, and
scores the result (immediate clears + spawn-induced clears + packing potential,
with a heavy game-over-on-spawn penalty). `choose_move(board, next_row=...)`. This
preview-aware lookahead is what took the live score past 100 comfortably.

### 7.7 Follow-up
- The 1-ply heuristic plays competently but is not optimal. A depth-2 lookahead
  is impossible without modelling the random next-row spawn; a depth-1 over
  legal moves + a tuned packing/colour-grouping heuristic is the practical ceiling.
- Wire as a periodic Playwright task (H5-first, dual-backend) gated by an
  `ws_token`/device flag; play once to game-over per day for the rank reward.
  Increase `move_pause` (≥0.6s) so the settle tween always finishes before the
  next read (avoids the benign "not_operable" early stop seen at pause=0.5s).
- ADB-backend port: the same world→screen mapping yields tap/swipe coords for
  `adb shell input swipe`; the board read would need the cocos view (H5) or a
  CNN, so H5 is the natural backend for this task.
