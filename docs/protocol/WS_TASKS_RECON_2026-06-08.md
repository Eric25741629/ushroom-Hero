# WS Tasks Recon — 家族大廳 / 烈炎山洞 / 開神燈 / 競技場跳戰 / 神秘商人 (2026-06-08)

> Static reverse-engineering of 5 features' click flow + WS protocol, for the `ws_token` backend roadmap.
> Source: bundle `docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`
> (`MSG_TO_ID_MAP` line 7835) + repo code/docs. Wire: see `docs/protocol/AUTH_HANDSHAKE_SPEC.md` + `ws_token/codec.py`.
>
> **Universal caveat (applies to ALL 5):** proto **field NUMBERS are NOT in the bundle** — the proto JSON is a
> runtime asset (`protobuf.Root.fromJSON`). Field *names* below are exact (from `netManager.send(...)` call
> sites); field *numbers/types* are inferred from declaration order and must be pinned by ONE live export:
> `netManager.protoRoot.lookupType('<family>.<name>_c2s').toJSON()` via `tools/_auth_capture_probe.py <port> --await`
> (the same CDP path used for creds). cmd ids ARE confirmed. c2s and s2c share one cmd id (direction by context).

---

## 1. 家族大廳 (Family / Guild Hall) — `guild.*` block 0x1D00

Hall is a **main-view panel** (`GuildMapSceneView`), not a popup. Action hub = `GuildView` popup (open via
`Infobg/btnInfo-001`). On entry the client auto-sends `guild_area_enter` (0x1D18).

| cmd | hex (dec) | purpose |
|-----|-----------|---------|
| `guild_info` | 0x1D01 (7425) | hall / overview |
| `guild_members_info` | 0x1D10 (7440) | member list |
| `guild_donate` | **0x1D11 (7441)** | 捐獻 (core daily; **empty body**, server picks tier) |
| `guild_area_enter` | 0x1D18 (7448) | enter map (auto) |
| `guild_help` | 0x1D1E (7454) | 幫人/協助求助 `{help_id}` |
| `guild_treasure_open` | 0x1D24 (7460) | 開寶箱/尋寶 `{n, round}` |
| `guild_rank_info` / `guild_rank_my_info` | 0x1D14 / 0x1D15 | 排名 / 我的排名 |
| `guild_get_message_notice_list` | 0x1D27 (7463) | 留言/公告 |

Full table (7425–7464) in the agent report; pay/防禦 are separate blocks (0x18AD-AE, 0x5218). Guild **BOSS**
(`guild_boss_*` 0x2001-0x200B) is a separate block — note "烈炎" in the hall routes to the *dungeon* league-solo
feature below, NOT guild_boss.

Key nodes: main-tab cell → `if HasGuild(): TryJumpToMainViewPanel(Main_Guild)`; `GuildView` buttons
`btnDonate`/`btnMemberDetail`/`btnHelp`/`btnMessage`/`btnSetting`. Errors via `error.error_info_s2c` 0x0201.
Reuse: `docs/protocol/CARPARK_GUILD_NODES.md` (node tree; this supersedes its cmd-name numbering with real ids),
memory `reference_carpark_guild_nodes`.

---

## 2. 烈炎山洞 (Family Blazing Cave) = guild Solo-Boss — `dungeon.dungeon_league_solo_*`

Two distinct actions (often conflated): **fight boss** (raises guild damage rank) vs **daily box claim**
(the "每天去領取").

| cmd | hex (dec) | purpose |
|-----|-----------|---------|
| `dungeon_league_solo_info` | 0x0E0E (3598) | normal info + box state (`{record_list, reward_status, box_list}`) |
| `dungeon_league_solo_get_reward` | 0x0E0F (3599) | **claim box** `{type}` (1-4) |
| `dungeon_league_solo_update_box_s2c` | 0x0E10 (3600) | claim ack `{box_info:[BoxEntry]}` |
| `dungeon_league_hard_info` / `_buff` | 0x0E18 / 0x0E17 | 噩夢 mode info |
| `dungeon_battle_start` (generic) | 0x0E07 (3591) | boss fight start `{type:6, level}` |
| `dungeon_battle_result` (generic) | 0x0E08 (3592) | fight result `{type, dungeon_id, result, ...}` |

`BoxEntry = {type, count, got_count}`; **`chest_limit` is NOT on the wire** — it's in config
`configLeague_solo_chapter_chest` / `configLeague_solo_hard_chest` keyed by guild level. box types: 1/2 = normal
普通/稀有, 3/4 = nightmare. Fight ticket = goods gtid 7002.

Flow (claim): GuildMapScene `btnBoss_1` → `guild_rank_my_info{rank_type:1}` → SingleEnterView `btnBox` →
`league_solo_info{}` → SingleBoxView `btnGet1/2` → `league_solo_get_reward{type}` → `update_box` ack.

⚠ **BUG in existing `utils/family_lieyan.py`** (standalone, not wired into new_main_v2): it reads
`data.box_info`/`boxes`/`list` but the info reply field is **`box_list`**; and it expects `chest_limit` inside
each entry (it's actually in the config tables) → its "maxed" check uses limit=0 and skips all boxes. Real guard:
`count>0 && count>got_count && got_count<chest_limit`. Fix or treat 0x0201 error 159 as "already claimed".
Reuse: `docs/protocol/LIEYAN_CAVE.md` (cmd ids correct; box field names need this correction), `tests/test_family_lieyan.py`.

---

## 3. 開神燈 (Lamp / equipment auto-open) — `equip.*` block 0x05xx

| cmd | hex (dec) | purpose |
|-----|-----------|---------|
| `equip_box_open_all` | **0x0509 (1289)** | open N / 連閃 `{num, quality}` → `{equip_ids[], reward[]}` |
| `equip_box_open` | 0x0508 (1288) | open one (empty body) → `{equip_id}` |
| `equip_change_s2c` | **0x0504 (1284)** | inventory-add push `{type, change_list[], sub_type}` — **the drop-detail frame `utils/equipment_cache.py` already parses** (sub-schema sf1/2/3/6/7/9 empirically confirmed live 2026-05-11) |
| `equip_shop` | 0x0505 (1285) | **sell** `{equip_ids}` (dump rejects) |
| `equip_offline_open_box_setting` | 0x051d (1309) | auto-open config `{quality, attr_condition_list, open_num, is_offline_open}` |
| `equip_wear` | 0x0502 (1282) | wear `{tab_id, equip_id}` |

Pure-WS flow: `(opt) 0x051d set threshold` → `0x0509 {num, quality:0}` → reply `{equip_ids, reward}` + `0x0504`
detail push + `0x0402` lamp item −20×N → decide keep/sell via `equipment_cache` rarity/affix compare →
`0x0505 {equip_ids:[rejects]}` → loop until lamp gtid 1001 exhausted.

**Key win:** over WS there is **no mandatory compare window** — keep = no-op, sell = 0x0505. This eliminates the
entire "殘留比較窗" bug class (memory `feedback_lamp_leftover_equipment`). No existing pure-WS opener;
`opengold_v2/lamp_service.py` is GUI/OCR + passive 0x0504 sniff. Reuse: `utils/equipment_cache.py`,
`utils/web_game_api.py` (`_walk_pb`, `decode_equip_template`, EQUIP_* maps), `utils/lamp_drop_watch.py`,
`opengold_v2/skill_evaluator.py` (keep/sell decision to port).

---

## 4. 競技場跳戰 (Arena PvP challenge) — `arena.*` module 20, 0x14xx

| cmd | hex (dec) | purpose |
|-----|-----------|---------|
| `arena_info` | 0x1401 (5121) | my rank/score + `enemy_list` (跳戰 targets) + `buy_times` |
| `arena_rank_list` | 0x1402 (5122) | leaderboard |
| `arena_combat` | **0x1403 (5123)** | **跳戰** `{eid}` → `{code, vid, seed, atk_data, def_data}` |
| `arena_result` | **0x1404 (5124)** | report outcome `{vid, wid}` → `{is_win, my_score_change, my_rank, ...}` |
| `arena_refresh` | 0x1406 (5126) | reroll opponents |
| `arena_role_info` | 0x1407 (5127) | inspect opponent `{role_id}` |
| `arena_buy` | 0x1409 (5129) | buy tickets `{chapter_type:5, buy_count}` (ticket gtid 1006) |

Flow: `info` → (opt `refresh`) → `combat{eid=enemy_list[i].id}` → **client simulates battle from `seed`+data**
→ `result{vid, wid=winner.id}` → settlement. ⚠ **Winner is client-decided** (server presumably validates —
trust boundary). No per-claim reward cmd (rank rewards via season/mail). Greenfield protocol; existing OCR
automation: `game_actions/daily_tasks.py:click_arena_challenges` (daily Task 10). NOTE `mushroom_arena`
(Task 13, 膜拜冠軍, 3-wk cycle) is a *different* feature.

---

## 5. 神秘商人 (Mysterious Merchant) = Mall tabs — `shop.*` module 0x1B (27)

NOT a standalone family — it's the Mall/商城 with a `shop_type` discriminator: **buy=23 (MysteryStoreBuy),
sell=22 (MysteryStoreSell)**. View `MysteryStoreView`, gated `FUNC_MYSTERYSHOP=105`.

| cmd | hex (dec) | purpose |
|-----|-----------|---------|
| `shop_info` | 0x1B01 (6913) | goods state `{shop_type}` → `{shop_type, buy_info:[{k=goods_id, v=bought_count}]}` |
| `shop_buy` | 0x1B02 (6914) | **buy AND sell** `{shop_type, shop_id, num}` → `{shop_id}` (+ global currency/bag sync) |
| `shop_limit_time` | 0x1B03 (6915) | (Parking/WeeklyBox only — NOT merchant) |

The goods *list* (price/icon/limit) is **client config `configMall`** (rows where `type`∈{22,23}); the server only
returns per-goods bought-counts. Remaining = `config.limit − buy_info[id]`. Escalating price from config
`add_price[]`. **No refresh cmd** — auto-resets per `refresh_way`. Buy failure likely via 0x0201 (unconfirmed).
⚠ Don't confuse with `MysteryControl` (神秘礦坑 mine + farm housekeeper). Greenfield.

---

## Field numbers — EXPORTED ✅ (2026-06-08, CDP 7fe98fc6 port 9226)

The universal blocker is resolved. Full field-numbered proto schemas exported via
`netManager.protoRoot.lookup('<fam>').toJSON()` (reading schema does NOT kick the session):

| file | content |
|------|---------|
| `docs/protocol/GUILD_PROTO_SCHEMA.json` | 72 types, 40 cmd ids |
| `docs/protocol/DUNGEON_PROTO_SCHEMA.json` | 43 types, 24 cmd ids (incl. 烈炎山洞 league_solo) |
| `docs/protocol/EQUIP_PROTO_SCHEMA.json` | 48 types, 27 cmd ids (神燈) |
| `docs/protocol/ARENA_PROTO_SCHEMA.json` | 18 types, 9 cmd ids |
| `docs/protocol/SHOP_PROTO_SCHEMA.json` | 6 types, 3 cmd ids (神秘商人 = Mall) |
| `docs/protocol/TYPE_PROTO_SCHEMA.json` | 368 shared `type.*` sub-messages (p_equip, p_arena_role, p_league_solo_box, p_head, p_reward, p_key_value, ...) |

Each file = `{family, cmd_ids:{name:id}, schema:{nested:{type:{fields:{name:{id,type,rule}}}}}}`. Every field
number/type above is now authoritative (the recon's "guessed" markers are superseded by these files).

**Key c2s/s2c (verified field numbers):**
- `dungeon_league_solo_info_s2c {box_list#1:p_league_solo_box[], record_list#2, reward_status#3}` ;
  `..._update_box_s2c {box_info#1}` ; `..._get_reward_c2s {type#1}`. `p_league_solo_box {type#1,count#2,got_count#3,rare_offer_name#4}` — **no chest_limit on wire (it's config)**. → **confirms the `utils/family_lieyan.py` bug** (it reads `box_info` from the *info* reply; the field is `box_list`).
- `equip_box_open_all_c2s {num#1,quality#2}` → `_s2c {equip_ids#1:uint64[], reward#2:p_reward[]}` ; `equip_change_s2c {type#1,sub_type#2,change_list#3:p_equip[]}` ; `p_equip {equip_id#1,config_id#2,equip_lv#3,location#4,tab#5,base_attr#6:p_key_value[],rand_attr#7:p_key_value[],refine_lv#8,power#9}` (base_attr=`equipment_cache` sf6, rand_attr=sf7) ; `equip_shop_c2s {equip_ids#1:uint64[]}` ; `equip_offline_open_box_setting_c2s {quality#1,attr_condition_list#2,open_num#3,is_offline_open#4}`.
- `arena_combat_c2s {eid#1:int64}` → `_s2c {code,vid,seed,atk_data,def_data}` ; `arena_result_c2s {vid#1:uint64,wid#2:uint64}` → `_s2c {is_win#1,my_score#2,my_rank#3,my_score_change#4,e_name#5,e_rank#6,e_score#7,e_score_change#8,e_head#9}` ; `arena_info_s2c {season#1,end_time#2,my_score#3,my_rank#4,enemy_list#5:p_arena_role[],buy_times#6}` (eid = `p_arena_role.id`).
- `shop_info_c2s {shop_type#1}` → `_s2c {shop_type#1,buy_info#2:p_key_value[]}` ; `shop_buy_c2s {shop_type#1,shop_id#2,num#3}` → `_s2c {shop_id#1,num#2}`.
- `guild_donate_c2s {}` (empty, confirmed) ; `guild_treasure_open_c2s {round#1,n#2}` ; `guild_help_c2s {help_id#1}` ; `guild_members_info_c2s {guild_id#1}`.
