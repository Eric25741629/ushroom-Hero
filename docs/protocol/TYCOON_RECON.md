# 傳奇大亨 (Legendary Tycoon, 大富翁/monopoly board) — WS Protocol Recon

Recon device: `emulator-5556` (菜雞), live CDP `http://127.0.0.1:9223`.
Dates: 2026-06-14 (initial, activity thought closed) + 2026-06-14 LIVE capture
(activity confirmed OPEN — banner rail had simply been collapsed). Method: Cocos
scene-walk + `netManager._protoClass` field-schema extraction + WS ring-buffer
hook on `sendMessage`/`reciveMsg` + TWO bounded live dice rolls. Decoded with
`ws_token.codec.walk`.

## TL;DR / Verdict (LIVE-CONFIRMED)

| Question | Answer |
|---|---|
| Is 傳奇大亨 ACTIVE? | **YES.** `txtTime` showed "4天19時34分35秒" remaining. Earlier `icon_4003.active:false` was just the COLLAPSED top-right banner rail; the activity view `ActivityMonopolyView` was loadable + live. |
| Protocol family / module | **`act` module 24** (cmd `0x18xx`). NOT act2/module 25 — the prior guess from a config-table name was wrong. The real protos are `act.act_monopoly_*`. |
| act_type value | **4003** (c2s body f1 == 4003, LIVE-verified). Banner name `icon_4003` matches. |
| Real cmd ids | `info` **0x18A8 (6312)**, `dice/roll` **0x18A9 (6313)**, `dice_time` push **0x18AA (6314)**, `task_update` push 0x180E (6158). `cmd//256 == 24`. |
| Dice server-authoritative or client-computed? | **SERVER-AUTHORITATIVE (live-confirmed).** Roll c2s is empty (`{act_type}` only); the roll s2c returns `dice_num` (the point), `pos` (new token tile = old pos + dice_num), and the landed reward. No client RNG, no `pos_list` to report. |
| Separate reward-claim cmd? | **NO.** Each roll's landed reward is auto-granted server-side (an `inventory 0x0402` push follows). "Auto-claim" == rolling while dice remain. (The 創業日誌 task RedPoint=9 is the global TASK system via `act.act_task_update`, NOT this module.) |
| `ws_token/tycoon.py` built? | **YES** — pure-WS module + `tests/test_ws_token_tycoon.py` (15 passing). |

## 1. Entry / UI

- Banner: `/UIRoot/NormalView/MainView/top/systemTop/btnRightRoot/icon_4003`
  (a `cc.Button`, `clickEvents.length == 0`). When the top rail is collapsed the
  parent `top` is `active:false`; expand it to reveal `icon_4003`.
- View class: **`ActivityMonopolyView`**. Key sub-nodes:
  - `view/cur/imgDice` (`cc.Button`) = roll button; `view/cur/txtNum` = dice count
    (was "30"); `view/cur/txtDesc` = "11:15:23 后恢复10个" (regen text).
  - `view/map/gridContent/grid1..grid20` = the 20 board tiles (each a `cc.Button`).
  - `view/task` = 創業日誌 (RedPoint showed "9" claimable — task system, not here).
  - `view/btnDoubleCard` = 翻倍卡 (Label "x2"); `view/shop` = 交易所; `view/nodeTime`
    = 剩餘時間 countdown.

Note: `imgDice` is a `cc.Button` with `clickEvents.length == 0`, so the live recon
roll was triggered both by `page.mouse.click` at its world coords AND (roll #2) by
sending the WS frame directly — both produced an identical server roll.

## 2. Protocol (LIVE-captured field schemas from `netManager._protoClass`)

`act` module 24. cmd = `module*256 + N`. c2s and s2c share the cmd id.

```
act.act_monopoly_info_c2s   6312 0x18a8 { act_type#1:uint32 }
act.act_monopoly_info_s2c          0x18a8 { act_type#1, circle#2, pos#3,
      dice_time#4, double_card_open#5, event#6:p_monopoly_event,
      grid_list#7:p_monopoly_grid[], reward_circle_list#8:uint32[] }
act.act_monopoly_dice_c2s   6313 0x18a9 { act_type#1:uint32 }     <- "roll" (empty but act_type)
act.act_monopoly_dice_s2c          0x18a9 { act_type#1, dice_num#2, circle#3,
      pos#4, event#5:p_monopoly_event, reward#6:p_monopoly_reward[] }
act.act_monopoly_dice_time_s2c 6314 0x18aa { act_type#1, dice_time#2 }  <- regen-timer push
act.act_task_update_s2c     6158 0x180e { type#1, task_list#2:p_act_task[] }  <- 創業日誌 push

p_monopoly_grid   { grid_id#1, cfg_id#2, type#3 }            (20 tiles; type=1)
p_monopoly_reward { type#1, item#2:p_key_value{k#1,v#2}, ?#3 } (landed-tile reward)
```

(There are exactly 5 `act_monopoly_*` protos registered — NO `*_reward_claim`,
NO `*_move` — confirming rewards are auto-granted and the token is advanced by the
server, not by a client-reported path.)

## 3. LIVE capture (account 菜雞, 2026-06-14)

Board info on open (`info_s2c` 0x18a8, hex `08a31f 1000 1801 2000 2800 3a06...x20`):
```
act_type=4003 circle=0 pos=1 dice_time=0 double_card_open=0
grid_list = 20 tiles, each {grid_id=N, cfg_id=N, type=1}  (N=1..20)
```

Roll #1 (mouse click on imgDice):
```
c2s 0x18a9  08 a3 1f                              {act_type=4003}
s2c 0x18a9  08a31f 1006 1800 2007 320b 0804 1205 08f90a1014 1800
            -> act_type=4003 dice_num=6 circle=0 pos=7
               reward[0]={type=4, item={k=1401, v=20}}
push 0x180e {act_type, {f1=400306, f2=400301, f3=20, f4=1}}  (reward-cfg event)
push 0x18aa {act_type, 1781389562}                            (dice regen ts)
push 0x0402 x2  -> item 1402 qty=29, item 1401 qty=20         (rewards landed)
```

Roll #2 (sent the WS frame directly through the game socket — pure-WS path):
```
c2s 0x18a9  08 a3 1f                              {act_type=4003}
s2c 0x18a9  08a31f 1003 1800 200a 320b 0804 1205 08f90a1014 1800
            -> dice_num=3 circle=0 pos=10  (= prev pos 7 + 3)
               reward[0]={type=4, item={k=1401, v=20}}
```

The token position advanced 1 -> 7 -> 10 exactly by the server-returned
`dice_num`, and the reward landed each time. Items 1401/1402 are the activity
currency pair flagged in `configActivity_term` (`[1401,1402]` tail).

## 4. Dice: server-authoritative — VERDICT EVIDENCE

The roll c2s carries NO seed / NO point / NO path — only `{act_type=4003}`. The
roll s2c carries the rolled `dice_num` and the resulting `pos`, and the matching
`0x0402` inventory pushes confirm the landed reward was credited by the server.
This is the same shape as the guild dice board (`guild_dice_point_s2c { point[],
reward_list[] }`) but even simpler: the monopoly board has NO separate
`*_area_move {pos_list}` report — the server moves the token itself. Therefore a
**pure-WS auto-roll is correct and safe**: send the empty roll, read
`dice_num`/`pos`/`reward`. No desync risk.

## 5. Implementation

`ws_token/tycoon.py` (NEW):
- Constants: `CMD_INFO=0x18A8`, `CMD_DICE=0x18A9`, `CMD_DICE_TIME=0x18AA`,
  `CMD_ERROR=0x0201`, `ACT_TYPE=4003`.
- `parse_board` -> `MonopolyBoard {act_type, circle, pos, dice_time,
  double_card_open, grids:[Grid], reward_circle_list}`.
- `parse_dice_result(cmd, body)` -> `DiceResult {ok, dice_num, circle, pos,
  rewards:[DiceReward{type,item_id,count}], error_code}` (0x0201 -> ok=False).
- `build_act_type_body`, `read_board` (plain call), `roll_dice`
  (`call_for(CMD_DICE, expect=(CMD_DICE, 0x0201))` — rejection never crashes),
  `auto_play(client, *, max_rolls=50, spacing=0.3)`: rolls while the server
  accepts, accumulates `total_rewards`, STOPS on the first 0x0201 or at
  `max_rolls`. Since rewards auto-grant, this single loop is both auto-roll and
  auto-claim.

Tests: `tests/test_ws_token_tycoon.py` (15 passing) — cmd constants, act_type,
body builder, `parse_board` (incl. 20-grid live shape + empty), `parse_dice_result`
(success + 0x0201), `read_board` body, `roll_dice` success + rejection, and
`auto_play` termination (server-rejection, max-rolls cap, zero-roll-on-first-reject).

## 6. Open items / follow-up

- The exact 0x0201 `error_code` when out of dice was NOT observed (only 30 dice in
  stock; recon stopped after 2 rolls per the bounded mandate). `auto_play` treats
  ANY 0x0201 as "stop", which is safe regardless of the specific code.
- The board carries NO explicit "dice remaining" field — the UI count is computed
  client-side from `dice_time` regen. The loop relies on the server's 0x0201
  rejection as the authoritative stop, not a count.
- 創業日誌 (`act.act_task_update` push, task_list) is the activity's task track,
  claimed through the GLOBAL task system (same split as couple's 默契考驗), not via
  this monopoly module. Wire separately if those task rewards are wanted.
- Wiring into `ws_token/runner.py` + a config flag is left to the orchestrator
  (another worker owns runner.py / config_manager.py).
