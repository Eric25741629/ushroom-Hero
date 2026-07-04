# 遺物 (Relic) point-allocation — LIVE protocol recon

> 2026-06-14 LIVE recon on 小寶 7fe98fc6 (web_h5, CDP 9226). One bounded
> upgrade was actually performed (user-authorized「此項可實際消耗」) to capture the
> opcode + verdict. No premium currency spent, no loop, no battle.

## TL;DR

- **遺物 is NOT part of 萬神試煉Beta (rogue module 76).** It is its OWN module
  **`relic` = module 17 (0x11)** with config tables `configRelic` / `configRelic_get`.
  The task hypothesis (rogue_attr_up 0x4c09 / rogue_science) was wrong — those are the
  roguelike's in-run attr/科技 systems, a different feature.
- The "point allocation" = **強化 (level-up) each relic**, spending **遺物碎片**
  (item 100022, currency id `7` — a non-premium grind currency, NOT gems).
- **Allocation cmd = `relic_up` 0x1103 (module 17, sub 3).**
  c2s carries ONLY the relic uid; server increments level by 1, deducts cost, and
  replies with the updated `p_relic`.
- **VERDICT: pure-WS, server-authoritative.** A forged WS `relic_up` is accepted.
  No client computation, no seed, no battle coupling, no anti-cheat. LIVE-verified.

## Module 17 (relic) cmd map

`netManager._protoClass` exposes 4 relic messages; cmd = module(17)*256 + sub:

| cmd | name | dir | body |
|---|---|---|---|
| `0x1101` (4353) | `relic.relic_info` | c2s `{}` / s2c | s2c `{ relic_list#1: p_relic[] }` |
| `0x1103` (4355) | `relic.relic_up` (上級/強化) | c2s `{ relic_uid#1:uint64 }` / s2c | s2c `{ p_relic#1 }` (the upgraded relic) |
| `0x1105` (4357) | `relic.relic_tab_info` | c2s `{}` / s2c | s2c `{ tab#1:uint32, tab_list#2: p_relic_tab_info[] }` |

Note: only `relic_info`, `relic_tab_info` are named in `_protoClass`; `relic_up`
(0x1103) is not in the JS proto-name table but its wire shape was LIVE-captured
(see below). subs 2/4 (likely relic_get/relic_equip/relic_plan ops) were NOT probed
to stay bounded — see "follow-up".

### Types (docs/protocol/TYPE_PROTO_SCHEMA.json, LIVE-confirmed)

```
p_relic { id#1:uint64, cfg_id#2:uint32, type#3:uint32, location#4:uint32, lv#5:uint32 }
  id       = unique instance id (e.g. 89562953023846)
  cfg_id   = relic catalog id 4001..4030 (configRelic col0)
  type     = quality 1..5 (configRelic col3)
  location = equipped slot 0=unequipped, 1..7 = position in active plan
  lv       = relic level 1..150

p_relic_tab_info { tab#1:uint32, name#2:string, pos_info#3: p_key_value[] }
  one entry per named loadout plan; pos_info maps position(1..7) -> relic cfg_id
```

## LIVE capture of the ONE bounded upgrade

Relic 羈絆面具 (uid 89562953023846, cfg_id 4017), Lv.99 → Lv.100, cost 442080 遺物碎片.

```
tx 0x1103  body = 08 e6828080d0ae14
           -> { f1 (relic_uid) = 89562953023846 }     # ONLY the uid

rx 0x0302  (currency-update push: 遺物碎片 new balance)
rx 0x0308  (multi-currency push)
rx 0x0324  (attribute-recalc push, 262B)
rx 0x1103  body = 0a11 08e6828080d0ae14 10b11f 1801 2001 2864
           -> { f1: p_relic{ id=89562953023846, cfg_id=4017, type=1,
                             location=1, lv=100 } }    # updated relic
```

UI before/after (RelicEditView): `Lv.99/150` → `Lv.100/150`;
遺物碎片 `38,637,595` → `38,195,515` (delta 442,080 = exact cost). Server-authoritative.

### Why this is a clean pure-WS verdict (vs the rogue battle case)

- c2s `relic_up` carries NO client-computed result, NO operators, NO seed — only the
  relic uid. The server alone decides the new level + cost. This is the opposite of
  `rogue_main_result` (which reports a client-simulated battle outcome).
- The server accepted the bare uid, deducted the grind currency, bumped the level, and
  pushed the new state. A `ws_token` client holding the auth ticket can replay this byte
  pattern to allocate relic points with no browser. (互踢 caveat: same account can't run
  the App's own WS + a ws_token WS at once — standard ws_token limitation.)

## UI path (cocos)

- Open: `uiMgr.openView('RelicMainView')` → `RelicMainView`
  (7 `relicItem1..7` per active plan; `btnFind`=寻找遗物 draw; `btnSwitch`=切换方案;
   `已獲得遺物 35/35`; top-right currency = 遺物碎片).
- Per-relic: click `relicItem{n}` → `RelicEditView`
  (`txtLevel` Lv.X/150; `btnUpgrade`=強化 +`txtCost`; `btnEquip`=装配/已裝備;
   `btnPrev`/`btnNext` cycle relics).
- The `btnUpgrade` click is what fires `relic_up` 0x1103. emit('click') works on these
  nodes (unlike RogueView which needs callbackInfos).

## Relic economics (from configRelic / configRelic_get)

- 35 relics (cfg_id 4001..4030 + a few 403x), each level 1..150.
- `configRelic[cfg_id][lv]`:
  - col8  = attr bonus `[[2001,V],[2003,V],[2005,V]]` (base 攻擊/防禦/生命),
            grows ~linearly with level (≈ +1600 per attr per level).
  - col10 = upgrade cost `[[7, C]]` (遺物碎片) to go FROM this level; rises with level
            then plateaus (e.g. 4017: lv98=394400, lv99/100/101=442080).
- `configRelic_get` = 30 escalating draw tiers (寻找遗物), pool widens by tier,
  cost `[[7, 300..57950]]`. (Acquisition, separate from leveling.)
- Total relic effect is the SUM over equipped relics (UI 總擁有效果 +62048%).

## "平均 / balanced" allocation strategy

Because (a) the active plan has exactly 7 equipped slots, (b) each level adds a
roughly CONSTANT stat (+~1600/attr/level), and (c) per-level cost in the working band
is roughly equal across relics, the variance-minimising / fragment-efficient policy is:

> **Always upgrade the LOWEST-level equipped relic next, until the budget (fragments)
> or a per-relic level cap is exhausted.** Ties broken by slot order. This keeps the 7
> relics' levels as equal as possible ("平均" = even distribution of levels).

Pure-function spec (implemented in `ws_token/relic.py::plan_balanced_upgrades`):

```
plan_balanced_upgrades(relics, fragments, *, cost_at, level_cap=150, max_steps=...) -> [uid, ...]
  relics    : list of (uid, level) for the EQUIPPED relics of the active plan
  fragments : available 遺物碎片 (currency 7)
  cost_at(level) -> int : fragments to upgrade FROM `level` to `level+1`
  returns an ordered list of relic uids to upgrade (one entry == one +1 step),
  greedily picking the current lowest-level relic each step, deducting its cost,
  stopping when no affordable / cap-reached relic remains.
```

This is deliberately a *plan* (a list of single +1 steps) so the live executor can do
ONE `relic_up` per step, re-reading `relic_info` between steps if desired, and stop
any time — matching the bounded, abortable execution style the project prefers.

## Follow-up still needed live (NOT done, to stay bounded)

- subs 0x1102 / 0x1104 (module 17): likely `relic_get` (draw, spends fragments per
  configRelic_get) and `relic_equip` / `relic_plan_set` (装配 / 切换方案). Capture by
  hooking WS while pressing `btnFind` (draw) and `btnEquip` / `切换方案`. Each is a
  single bounded press. Not captured here because they were out of the Task-9
  "point allocation" core (leveling) and 寻找遗物 would consume fragments on a draw.
- Confirm the `relic_up` server reply on FAILURE (insufficient fragments / max level):
  expected `0x0201` error frame — not observed (we had ample fragments + headroom).
  `ws_token/relic.py` already treats a non-0x1103 / 0x0201 reply as failure.
- `relic_info` `location` semantics vs the 10 named plans: `relic_tab_info` carries the
  per-plan position→cfg_id map; `relic_info.location` is the slot in the *currently
  active* plan (tab f1). Cross-plan equip likely needs the plan-set cmd (above).
```
