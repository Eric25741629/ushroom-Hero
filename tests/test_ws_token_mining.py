"""Tests for ws_token.mining + ws_token.mining_adapter — OFFLINE only.

The board/builders/inventory/grid layers are exercised against synthetic
home_mine_info bodies built with the ws_token codec; NO real socket and NO
``plan_v4`` call (importing the planner is safe, but we keep tests decoupled
from miner so they never risk pulling torch/cv2). The dig orchestrators
(``dig``/``get_reward``) are intentionally NOT exercised — mining live is
human-supervised because it mutates the real board / burns pickaxes.

Protocol (docs/protocol/HOME_PROTO_SCHEMA.json + TYPE_PROTO_SCHEMA.json):
  home_mine_info_s2c {max_num#1, next_time#2, area#3, baseline#4, actives#5:uint32[],
                      area_info#6:p_key_value[], blocks#7:p_mine_block[], holes#8:p_mine_hole[]}
  p_mine_block       {id#1, x#2, y#3, config_id#4, count#5, is_reward#6}
  p_mine_hole        {config_id#1, last_num#2, max_num#3, hole_num#4}
  p_key_value        {k#1, v#2}
  home_mine_use_goods_c2s {goods_id#1, block_id#2}
  home_mine_get_reward_c2s {block_id#1}
  home_mine_auto_use_goods_c2s {gtid#1:int32, auto_type#2, num#3:int32}
Inventory現量 only from 0x0402 push (evt 9800001 consume / 9800009 gain).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec, mining  # noqa: E402
from ws_token.mining import (  # noqa: E402
    GOODS_BOMB,
    GOODS_DRILL,
    GOODS_PICKAXE,
    InventoryTracker,
    MineBlock,
    MineBoard,
    MineHole,
    build_auto_use_goods_body,
    build_get_reward_body,
    build_use_goods_body,
    parse_board,
)
from ws_token.mining_adapter import (  # noqa: E402
    board_to_grid,
    grid_pos_to_block_id,
    plan as plan_ws_mining,
)


# --- body builders (mirror the live wire layout for synthetic fixtures) -----

def _block(block_id, x, y, config_id, count, is_reward=0):
    return (codec.pb_uint(1, block_id) + codec.pb_uint(2, x) + codec.pb_uint(3, y)
            + codec.pb_uint(4, config_id) + codec.pb_uint(5, count)
            + codec.pb_uint(6, is_reward))


def _hole(config_id, last_num, max_num, hole_num):
    return (codec.pb_uint(1, config_id) + codec.pb_uint(2, last_num)
            + codec.pb_uint(3, max_num) + codec.pb_uint(4, hole_num))


def _kv(k, v):
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _mine_info_body(*, max_num, next_time, area, baseline, actives=(),
                    area_info=(), blocks=(), holes=()):
    out = (codec.pb_uint(1, max_num) + codec.pb_uint(2, next_time)
           + codec.pb_uint(3, area) + codec.pb_uint(4, baseline))
    for a in actives:
        out += codec.pb_uint(5, a)
    for kv in area_info:
        out += codec.pb_msg(6, kv)
    for b in blocks:
        out += codec.pb_msg(7, b)
    for h in holes:
        out += codec.pb_msg(8, h)
    return out


# --- parse_board ------------------------------------------------------------

def test_parse_board_top_level_scalars():
    body = _mine_info_body(max_num=114, next_time=1780000000, area=3, baseline=11003900)
    board = parse_board(body)
    assert isinstance(board, MineBoard)
    assert board.max_num == 114
    assert board.next_time == 1780000000
    assert board.area == 3
    assert board.baseline == 11003900


def test_parse_board_decodes_actives_repeated():
    body = _mine_info_body(max_num=1, next_time=0, area=1, baseline=0,
                           actives=(101, 202, 303))
    board = parse_board(body)
    assert board.actives == [101, 202, 303]


def test_parse_board_decodes_blocks_with_all_fields():
    blk = _block(block_id=390006, x=6, y=3900, config_id=201, count=2, is_reward=1)
    body = _mine_info_body(max_num=1, next_time=0, area=1, baseline=3900, blocks=(blk,))
    board = parse_board(body)
    assert len(board.blocks) == 1
    b = board.blocks[0]
    assert isinstance(b, MineBlock)
    assert (b.block_id, b.x, b.y, b.config_id, b.count, b.is_reward) == (390006, 6, 3900, 201, 2, 1)


def test_parse_board_decodes_holes():
    h = _hole(config_id=7001, last_num=4, max_num=10, hole_num=2)
    body = _mine_info_body(max_num=1, next_time=0, area=1, baseline=0, holes=(h,))
    board = parse_board(body)
    assert len(board.holes) == 1
    hole = board.holes[0]
    assert isinstance(hole, MineHole)
    assert (hole.config_id, hole.last_num, hole.max_num, hole.hole_num) == (7001, 4, 10, 2)


def test_parse_board_decodes_area_info_key_values():
    body = _mine_info_body(max_num=1, next_time=0, area=2, baseline=0,
                           area_info=(_kv(1, 5), _kv(2, 9)))
    board = parse_board(body)
    assert board.area_info == {1: 5, 2: 9}


def test_parse_board_empty_body_is_safe():
    board = parse_board(b"")
    assert board.blocks == [] and board.holes == [] and board.actives == []


# --- builders ---------------------------------------------------------------

def test_build_use_goods_body_goods_first_then_block():
    # home_mine_use_goods_c2s {goods_id#1, block_id#2}: goods first, then block.
    assert build_use_goods_body(GOODS_PICKAXE, 390006) == (
        codec.pb_uint(1, 4001) + codec.pb_uint(2, 390006))


def test_build_use_goods_body_round_trips_via_walk():
    body = build_use_goods_body(GOODS_BOMB, 12345)
    d = codec.walk_dict(body)
    assert d[1] == GOODS_BOMB == 4003
    assert d[2] == 12345


def test_build_get_reward_body_single_block_field():
    assert build_get_reward_body(390006) == codec.pb_uint(1, 390006)


def test_build_auto_use_goods_body_three_fields():
    # home_mine_auto_use_goods_c2s {gtid#1, auto_type#2, num#3}
    body = build_auto_use_goods_body(gtid=4001, auto_type=1, num=20)
    d = codec.walk_dict(body)
    assert d == {1: 4001, 2: 1, 3: 20}


def test_goods_ids_match_mining_schema():
    assert (GOODS_PICKAXE, GOODS_DRILL, GOODS_BOMB) == (4001, 4002, 4003)


def test_send_dig_uses_send_only_use_goods_body():
    class FakeClient:
        def __init__(self):
            self.sent = []

        def send(self, cmd, body):
            self.sent.append((cmd, body))

    client = FakeClient()

    mining.send_dig(client, GOODS_PICKAXE, 390006)

    assert client.sent == [(0x0C03, build_use_goods_body(GOODS_PICKAXE, 390006))]


# --- InventoryTracker (fed synthetic 0x0402 pushes) -------------------------

def _inv_push(evt_type, *entries):
    """0x0402 body {event_type#1, items#2:{item_id#1, uid#2, new_count#3}[]}."""
    out = codec.pb_uint(1, evt_type)
    for item_id, new_count in entries:
        sub = (codec.pb_uint(1, item_id) + codec.pb_uint(2, 9999999999)
               + codec.pb_uint(3, new_count))
        out += codec.pb_msg(2, sub)
    return out


def test_inventory_tracker_consume_event_updates_count():
    tracker = InventoryTracker()
    tracker.on_push(0x0402, _inv_push(9800001, (4001, 113)))
    assert tracker.pickaxe == 113


def test_inventory_tracker_gain_event_updates_count():
    tracker = InventoryTracker()
    tracker.on_push(0x0402, _inv_push(9800009, (4002, 181)))
    assert tracker.drill == 181


def test_inventory_tracker_snapshot_event_updates_count():
    tracker = InventoryTracker()
    tracker.on_push(0x0402, _inv_push(9800004, (4001, 35)))

    assert tracker.pickaxe == 35
    assert tracker.has_item(4001) is True


def test_inventory_tracker_tracks_all_three_props():
    tracker = InventoryTracker()
    tracker.on_push(0x0402, _inv_push(9800001, (4001, 50), (4002, 60), (4003, 70)))
    assert (tracker.pickaxe, tracker.drill, tracker.bomb) == (50, 60, 70)
    assert tracker.counts == {4001: 50, 4002: 60, 4003: 70}


def test_inventory_tracker_ignores_unrelated_event_type():
    tracker = InventoryTracker()
    tracker.on_push(0x0402, _inv_push(5011, (4001, 999)))  # currency change, not item
    assert tracker.pickaxe == 0
    assert tracker.counts == {}


# --- seed_from_query: full snapshot via 0x0401 request/response --------------

def _inv_query_reply(*entries):
    """0x0401 reply: repeated top-level entry#1 {item_id#1, uid#2, count#3}.

    Unlike 0x0402 (delta push wrapped in {evt_type#1, items#2}), the full
    snapshot is a flat repeated list — count lives in f3 (live-confirmed on
    5554: 6017=522, 6019=78, 6020=78, 6021=1078, 4001=7).
    """
    out = b""
    for item_id, count in entries:
        sub = (codec.pb_uint(1, item_id) + codec.pb_uint(2, 89_000_000_000)
               + codec.pb_uint(3, count))
        out += codec.pb_msg(1, sub)
    return out


class _QueryClient:
    def __init__(self, reply, reply_cmd=0x0401):
        self._reply = reply
        self._reply_cmd = reply_cmd
        self.calls = []

    def call_for(self, cmd, body, expect_cmds=(), timeout=None):
        self.calls.append((cmd, body, tuple(expect_cmds)))
        return self._reply_cmd, self._reply


def test_seed_from_query_populates_full_inventory():
    tracker = InventoryTracker()
    client = _QueryClient(_inv_query_reply((4001, 7), (6017, 522), (6021, 1078)))
    n = tracker.seed_from_query(client)
    assert n == 3
    assert tracker.counts == {4001: 7, 6017: 522, 6021: 1078}
    assert tracker.pickaxe == 7
    assert tracker.has_item(6017) is True       # workshop material now visible
    # asked the server for the full-snapshot cmd with an empty body
    assert client.calls[0][0] == mining.CMD_INVENTORY_QUERY
    assert client.calls[0][1] == b""


def test_seed_from_query_empty_reply_is_noop():
    tracker = InventoryTracker()
    assert tracker.seed_from_query(_QueryClient(b"")) == 0
    assert tracker.counts == {}


def test_seed_from_query_wrong_reply_cmd_is_noop():
    tracker = InventoryTracker()
    client = _QueryClient(_inv_query_reply((4001, 7)), reply_cmd=0x0201)
    assert tracker.seed_from_query(client) == 0
    assert tracker.counts == {}


def test_seed_from_query_does_not_clobber_then_delta_updates():
    """Snapshot seeds the baseline; a later 0x0402 consume delta still applies."""
    tracker = InventoryTracker()
    tracker.seed_from_query(_QueryClient(_inv_query_reply((4001, 7))))
    assert tracker.pickaxe == 7
    tracker.on_push(0x0402, _inv_push(9800001, (4001, 6)))  # dug one
    assert tracker.pickaxe == 6


def test_inventory_tracker_ignores_unrelated_cmd():
    tracker = InventoryTracker()
    tracker.on_push(0x0504, _inv_push(9800001, (4001, 5)))  # lamp drop push, not inventory
    assert tracker.pickaxe == 0


def test_inventory_tracker_latest_count_wins():
    tracker = InventoryTracker()
    tracker.on_push(0x0402, _inv_push(9800001, (4001, 113)))
    tracker.on_push(0x0402, _inv_push(9800001, (4001, 112)))
    assert tracker.pickaxe == 112


# --- mining_adapter.board_to_grid (NO plan_v4 call) -------------------------

def _board(baseline, blocks, actives=()):
    return MineBoard(max_num=114, next_time=0, area=1, baseline=baseline,
                     actives=list(actives), area_info={}, blocks=blocks, holes=[])


def test_board_to_grid_shape_is_7x6():
    grid = board_to_grid(_board(0, []))
    assert len(grid) == 7
    assert all(len(row) == 6 for row in grid)


def test_board_to_grid_empty_board_all_empty_label():
    grid = board_to_grid(_board(0, []))
    assert all(cell == "empty" for row in grid for cell in row)


def test_board_to_grid_dirt_terrain_label():
    # live H5/CDP: config_id 201 = 泥土 (1 hit) -> "dirt".
    blk = MineBlock(block_id=16238306, x=6, y=162383, config_id=201, count=1, is_reward=0)
    grid = board_to_grid(_board(162388, [blk]))
    # visible top is baseline - 5; x=6 -> col 5 (1-indexed cols).
    assert grid[0][5] == "dirt"


def test_board_to_grid_rock_terrain_label():
    # live H5/CDP: config_id 202 = 石頭 (>=2 hits) -> "rock".
    blk = MineBlock(block_id=16238301, x=1, y=162383, config_id=202, count=2, is_reward=0)
    grid = board_to_grid(_board(162388, [blk]))
    assert grid[0][0] == "rock"


def test_board_to_grid_count_drives_air_vs_solid():
    # p_mine_block.f5 ("count") 語意 LIVE 坐實 (CDP dig 2026-06-20, 小寶, 201 與 202 皆同):
    #   count==0 = 已挖空氣（挖它=no-op：0x0c03 無回覆、版面不變、不耗鏟）→ 投影成 empty。
    #   count>0  = 未挖實心（201=土, 202=岩, 401=礦）。
    # 舊版不看 count、把已挖空氣的 202 也標 rock → 盤面誤判為密集。
    air = MineBlock(block_id=16238301, x=1, y=162383, config_id=202, count=0, is_reward=0)
    assert board_to_grid(_board(162388, [air]))[0][0] == "empty", "count==0 stone = 已挖空氣"
    for count in (1, 2):
        rock = MineBlock(block_id=16238301, x=1, y=162383, config_id=202,
                         count=count, is_reward=0)
        assert board_to_grid(_board(162388, [rock]))[0][0] == "rock", f"未挖石頭 count={count}"


def test_board_to_grid_pit_terrain_label():
    # config_id 401 = 礦洞 -> reachable_pit (planner reward target)
    blk = MineBlock(block_id=16238303, x=3, y=162383, config_id=401, count=1, is_reward=1)
    grid = board_to_grid(_board(162388, [blk]))
    assert grid[0][2] == "reachable_pit"


def test_board_to_grid_depth_maps_to_rows():
    # live H5/CDP: baseline=162388 shows y=162383..162389 as rows 0..6.
    baseline = 162388
    top = baseline - 5
    blocks = [MineBlock(block_id=(top + r) * 100 + 1, x=1, y=top + r,
                        config_id=201, count=1, is_reward=0)
              for r in range(7)]
    grid = board_to_grid(_board(baseline, blocks))
    for r in range(7):
        assert grid[r][0] == "dirt", f"row {r} expected dirt"


def test_board_to_grid_block_outside_viewport_ignored():
    # y far below the 7-row viewport must not raise / must be dropped.
    blk = MineBlock(block_id=16239001, x=1, y=162390, config_id=201, count=1, is_reward=0)
    grid = board_to_grid(_board(162388, [blk]))
    assert all(cell == "empty" for row in grid for cell in row)


def test_board_to_grid_actives_without_features_are_unknown_solid():
    # live 0x0c01: actives lists valid dig targets; blocks only carries known
    # terrain features. An active cell with NO block feature = 未挖泥土 (undug dirt)
    # per CDP dig 2026-06-20 + MINING_SCHEMA L204. It must be SOLID (not empty), or
    # the planner thinks row 6 is already open and emits no progress step.
    baseline = 162390
    active_block_id = grid_pos_to_block_id(baseline, row=6, col=3)
    grid = board_to_grid(_board(baseline, [], actives=[active_block_id]))
    assert grid[6][3] == "dirt"


def test_plan_uses_active_cells_to_make_ws_progress_step():
    baseline = 162390
    active_block_id = grid_pos_to_block_id(baseline, row=6, col=3)
    board = _board(baseline, [], actives=[active_block_id])

    result = plan_ws_mining(board, {"pickaxe": 2, "drill": 0, "bomb": 0})

    assert result["ws_steps"], result
    first = result["ws_steps"][0]
    assert first["type"] == "dig"
    assert first["block_id"] == active_block_id


def test_grid_pos_to_block_id_round_trips_depth_col():
    # block_id = depth*100 + col, depth = baseline - 5 + row, col = x (1-indexed).
    assert grid_pos_to_block_id(baseline=162388, row=0, col=2) == 16238303
    assert grid_pos_to_block_id(baseline=162388, row=6, col=3) == 16238904


def test_plan_hold_floor_ignores_collected_row0_pit():
    """已採集 (count==0) 的 row-0 礦坑不該觸發 hold_floor。

    fc 死結根因：_block_label 把所有 config 401 標 reachable_pit（不看 count），
    舊版 hold_floor = count_remaining_pits(grid[0:1])>0 → 已收掉的 row-0 坑仍卡住
    hold_floor=True → 只能挑不開 floor-7 的格 → server 拒絕 → 永遠 unconfirmed。
    修法用原始 blocks 的 count>0 判定，count==0 不算。
    """
    baseline = 162390
    top = baseline - 5  # row 0 depth
    # row-0 一個已採集礦坑 (count==0)；row-6 放一個 active 讓 floor-7 關閉
    # （active 觸發 _project_board 的 unreachable_empty 填充，等同真實 fc 盤面）。
    collected_pit = MineBlock(block_id=top * 100 + 1, x=1, y=top,
                              config_id=401, count=0, is_reward=0)
    floor_active = grid_pos_to_block_id(baseline, row=6, col=3)
    board = _board(baseline, [collected_pit], actives=[floor_active])

    result = plan_ws_mining(board, {"pickaxe": 5, "drill": 0, "bomb": 0})

    assert result["hold_floor"] is False, result.get("hold_floor")


def test_plan_hold_floor_holds_for_uncollected_reachable_row0_pit():
    """未採集 (count>0) 且「在 actives 上（可挖）」的 row-0 礦坑仍要 hold_floor。"""
    baseline = 162390
    top = baseline - 5
    live_pit = MineBlock(block_id=top * 100 + 1, x=1, y=top,
                         config_id=401, count=1, is_reward=1)
    floor_active = grid_pos_to_block_id(baseline, row=6, col=3)
    # 礦坑本身在 actives（伺服器接受挖）才值得守住不捲動。
    board = _board(baseline, [live_pit], actives=[live_pit.block_id, floor_active])

    result = plan_ws_mining(board, {"pickaxe": 5, "drill": 0, "bomb": 0})

    assert result["hold_floor"] is True, result.get("hold_floor")


def test_plan_hold_floor_releases_unreachable_row0_pit():
    """未採集但「不在 actives（已被挖過頭、無法再挖）」的 row-0 礦坑不該 hold_floor。

    真實死結（7fe98fc6 2026-06-20）：礦坑被挖出的空洞越過後卡在視窗頂列，count>0
    但不在 actives（伺服器不接受挖）。舊版仍 hold_floor=True → 拒絕捲動 → 監督迴圈
    只能挑不開 floor-7 的深層 frontier 格狂挖，26 步燒掉 ~26 把鎬子，礦坑始終收不到、
    最後照樣捲走。守一個挖不到的坑只是純浪費，應放行捲動。
    """
    baseline = 162390
    top = baseline - 5
    stranded_pit = MineBlock(block_id=top * 100 + 1, x=1, y=top,
                             config_id=401, count=1, is_reward=1)
    floor_active = grid_pos_to_block_id(baseline, row=6, col=3)
    # 礦坑 block_id 刻意不放進 actives → 不可挖。
    board = _board(baseline, [stranded_pit], actives=[floor_active])

    result = plan_ws_mining(board, {"pickaxe": 5, "drill": 0, "bomb": 0})

    assert result["hold_floor"] is False, result.get("hold_floor")
