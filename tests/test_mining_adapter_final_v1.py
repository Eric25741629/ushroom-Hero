"""WS final_v1 projection: 21-row known board, action-aware valid targets,
dispatch, seven-row degradation, shadow isolation, and v1 fallback."""
from ws_token import mining, mining_adapter


def _block(depth, col, config_id, count=1, is_reward=0):
    return mining.MineBlock(depth * 100 + col, col, depth, config_id, count, is_reward)


def _board(*, baseline=105, area_info=None, actives=None, blocks=None):
    return mining.MineBoard(
        max_num=20, next_time=0, area=0, baseline=baseline,
        actives=list(actives or []), area_info=dict(area_info or {}),
        blocks=list(blocks or []), holes=[],
    )


def test_known_board_extends_to_covered_area_info_but_caps_at_21_rows(monkeypatch):
    board = _board(area_info={14: 1, 15: 2, 16: 3})
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda depth, col, info: 201)
    projected = mining_adapter.build_final_v1_input(board, {"pickaxe": 5, "bomb": 1, "drill": 1})
    assert len(projected["board"]) == 21
    assert projected["visible_rows"] == 7


def test_raw_offscreen_pit_overlays_static_terrain(monkeypatch):
    top = mining_adapter.viewport_top_depth(105)
    pit = _block(top + 8, 3, mining.TERRAIN_PIT, count=1, is_reward=1)
    board = _board(blocks=[pit])
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda depth, col, info: 201)
    projected = mining_adapter.build_final_v1_input(board, {})
    assert projected["board"][8][2] == "unreachable_pit"


def test_valid_targets_distinguish_pickaxe_frontier_from_item_air_placement():
    top = mining_adapter.viewport_top_depth(105)
    solid = _block(top + 1, 3, mining.TERRAIN_DIRT, count=1)
    air = _block(top, 3, mining.TERRAIN_DIRT, count=0)
    board = _board(actives=[solid.block_id], blocks=[solid, air])
    valid = mining_adapter.build_final_v1_input(board, {})["valid_targets"]
    assert ("dig", "pickaxe", 1, 2) in valid
    assert ("use", "bomb", 0, 2) in valid
    assert ("use", "drill", 0, 2) in valid
    assert ("dig", "pickaxe", 0, 2) not in valid


def test_plan_final_v1_maps_only_legal_first_step_to_ws_block_id(monkeypatch):
    # 完整 21 列重建（terrain_at 全覆蓋）→ 走 final_v1；7 列不完整盤另有 v1 短路測試。
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda d, c, info: 201)
    captured = {}
    monkeypatch.setattr(
        "miner.final_v1.plan_final_v1",
        lambda *args, **kwargs: captured.update(kwargs) or {
            "steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}],
            "score_breakdown": {"total": 1},
        },
    )
    board = _board(actives=[10103])
    result = mining_adapter.plan(board, {"pickaxe": 5}, planner_version="final_v1")
    assert captured["visible_rows"] == 7
    assert result["ws_steps"][0]["block_id"] == mining_adapter.grid_pos_to_block_id(105, 1, 2)


def test_incomplete_known_projection_degrades_to_seven_rows(monkeypatch):
    board = _board(area_info={14: 1, 16: 3})
    top = mining_adapter.viewport_top_depth(board.baseline)
    monkeypatch.setattr(
        mining_adapter.mine_terrain,
        "terrain_at",
        lambda depth, col, info: 201 if depth < top + 7 else None,
    )
    projected = mining_adapter.build_final_v1_input(board, {"pickaxe": 5})
    assert len(projected["board"]) == 7
    assert projected["projection_mode"] == "visible_only"


def test_empty_final_v1_result_falls_back_to_v1(monkeypatch):
    # 完整 21 列盤：final_v1 有跑但回空步 → 走 v1_fallback（有別於 7 列短路）。
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda d, c, info: 201)
    calls = []

    def fake_named(name, projected, inventory):
        calls.append(name)
        if name == "final_v1":
            return {"steps": []}
        return {"steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}]}

    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(_board(), {"pickaxe": 5}, planner_version="final_v1")
    assert calls == ["final_v1", "v1"]
    assert result["planner_source"] == "v1_fallback"


def test_incomplete_rebuild_routes_final_v1_through_v1(monkeypatch):
    # 只剩 7 列視野（21 列重建失敗）→ 該輪根本不叫 final_v1，直接走 v1 並標
    # planner_source="v1_7row_fallback" 供 telemetry 辨識。
    monkeypatch.setattr(
        mining_adapter.mine_terrain, "terrain_at",
        lambda depth, col, info: None,  # 無任何延伸列 → 盤面固定 7 列
    )
    calls = []

    def fake_named(name, projected, inventory):
        calls.append(name)
        assert len(projected["board"]) == 7
        return {"steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}]}

    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(_board(actives=[10103]), {"pickaxe": 5},
                                 planner_version="final_v1")
    assert calls == ["v1"]  # final_v1 從未被呼叫
    assert result["planner_source"] == "v1_7row_fallback"
    assert result["planner_name"] == "v1"


def test_full_21row_rebuild_runs_final_v1(monkeypatch):
    # 完整 21 列重建 → 照常走 final_v1（planner_source="planner"）。
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda d, c, info: 201)
    calls = []

    def fake_named(name, projected, inventory):
        calls.append(name)
        assert len(projected["board"]) == 21
        return {"steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}]}

    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(_board(actives=[10103]), {"pickaxe": 5},
                                 planner_version="final_v1")
    assert calls == ["final_v1"]
    assert result["planner_source"] == "planner"
    assert result["planner_name"] == "final_v1"


def test_final_v1_called_with_exec_profile_step(monkeypatch):
    # WS 監督迴圈每步重規劃取第一步 = step 語意；adapter 必須以 exec_profile="step"
    # 呼叫 plan_final_v1，才會啟用 KPI 對齊的 action_cost。需完整 21 列盤才走 final_v1。
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda d, c, info: 201)
    captured = {}
    monkeypatch.setattr(
        "miner.final_v1.plan_final_v1",
        lambda *args, **kwargs: captured.update(kwargs) or {
            "steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}],
        },
    )
    board = _board(actives=[10103])
    mining_adapter.plan(board, {"pickaxe": 5}, planner_version="final_v1")
    assert captured["exec_profile"] == "step"


def test_dug_pit_tracker_marks_prior_pit_now_collected(monkeypatch):
    top = mining_adapter.viewport_top_depth(105)
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda d, c, info: 201)
    session = mining_adapter.DugPitTracker()

    # 前輪：某格是活躍礦坑（count>0）。
    live_pit = _block(top + 2, 3, mining.TERRAIN_PIT, count=1, is_reward=1)
    session.observe(_board(actives=[live_pit.block_id], blocks=[live_pit]))

    # 本輪：同格已採集（count==0）＋ 一格從未是礦坑的空氣（count==0 dirt）。
    dug = _block(top + 2, 3, mining.TERRAIN_PIT, count=0, is_reward=1)
    never_pit_air = _block(top + 1, 5, mining.TERRAIN_DIRT, count=0)
    board2 = _board(blocks=[dug, never_pit_air])
    session.observe(board2)

    projected = mining_adapter.build_final_v1_input(board2, {})
    session.annotate(projected["board"], top)

    assert projected["board"][2][2] == "dug_pit"   # 曾為礦坑、今空 → dug_pit
    assert projected["board"][1][4] == "empty"      # 從未是礦坑 → 保持 empty


def test_new_session_clears_dug_pit_side_table(monkeypatch):
    top = mining_adapter.viewport_top_depth(105)
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda d, c, info: 201)

    # 第一個 session 觀測到 count>0 → count==0 的轉態。
    s1 = mining_adapter.DugPitTracker()
    s1.observe(_board(blocks=[_block(top + 2, 3, mining.TERRAIN_PIT, count=1, is_reward=1)]))
    s1.observe(_board(blocks=[_block(top + 2, 3, mining.TERRAIN_PIT, count=0, is_reward=1)]))

    # 新 session：面對相同的 count==0 盤面，沒有跨輪記憶 → 不標 dug_pit。
    s2 = mining_adapter.DugPitTracker()
    board = _board(blocks=[_block(top + 2, 3, mining.TERRAIN_PIT, count=0, is_reward=1)])
    s2.observe(board)
    projected = mining_adapter.build_final_v1_input(board, {})
    s2.annotate(projected["board"], top)

    assert projected["board"][2][2] == "empty"


def test_shadow_exception_is_logged_in_result_and_primary_plan_survives(monkeypatch):
    expected_primary_steps = [{"type": "dig", "target": (1, 2), "step_cost": 1}]

    def fake_named(name, projected, inventory):
        if name == "final_v1":
            raise RuntimeError("shadow boom")
        return {"steps": expected_primary_steps}

    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(
        _board(), {"pickaxe": 5}, planner_version="v1",
        shadow_planner_version="final_v1",
    )
    assert result["shadow"]["ok"] is False
    assert result["ws_steps"][0]["target"] == expected_primary_steps[0]["target"]
