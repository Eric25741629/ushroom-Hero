from dragon_realm.planner import decide, Action, Prefs
from dragon_realm.state import DragonState
from dragon_realm import constants as C

KEY2 = 1518   # 進第2層鑰匙 gtid（fixture 佔位；實際值見 config_kv.json）
KEY3 = 1519   # 進第3層鑰匙 gtid


class Cfg:
    tier_two_require = (KEY2, 1)
    tier_three_require = (KEY3, 1)
    stamina_tier = (5, 8, 10)     # 每層每次探索體力
    back_kill_cooldown = 600
    chest_key = {7001: 1520}      # event_id -> 鑰匙 gtid
    stamina_item_gtid = 1521


PREFS = Prefs(my_role_id=777, assist_teammates=True, auto_open_box=True)


def _state(**over):
    base = dict(activity_open=True, ceng=1, hp=30, server_time=10000, help_hp=0,
                event_id=0, event_type=0, event_uid=0, is_challenge=False,
                is_ask_help=False, back_kill_time=0, event_list=(), help_events=(), bag={})
    base.update(over)
    return DragonState(**base)


# --- 探索 / 體力 ---
def test_no_event_with_stamina_explores():
    assert decide(_state(hp=30), Cfg, PREFS).kind == C.A_EXPLORE

def test_out_of_stamina_stops_without_using_item():
    a = decide(_state(hp=4, bag={1521: 99}), Cfg, PREFS)   # 體力<5 但有體力道具
    assert a.kind == C.A_STOP
    assert a.reason == "out_of_stamina"

# --- 進層 / 終止覆寫 ---
def test_layer1_with_key_enters_layer2():
    a = decide(_state(ceng=1, bag={KEY2: 1}), Cfg, PREFS)
    assert a.kind == C.A_ENTER_CENG and a.ceng == 2

def test_layer2_reaching_tier3_gate_stops_not_enter():
    a = decide(_state(ceng=2, bag={KEY3: 1}), Cfg, PREFS)
    assert a.kind == C.A_STOP and a.reason == "reached_tier_three_gate"

def test_layer2_without_tier3_key_keeps_exploring():
    assert decide(_state(ceng=2, hp=30, bag={}), Cfg, PREFS).kind == C.A_EXPLORE

# --- 怪物 ---
def test_monster_killable_advances():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=False), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

def test_monster_challenge_asks_help():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=True, is_ask_help=False), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ASK_HELP

def test_monster_already_asked_waits_until_cooldown():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=True,
                      is_ask_help=True, back_kill_time=9999, server_time=10000), Cfg, PREFS)
    assert a.kind == C.A_WAIT

def test_monster_already_asked_rekills_after_cooldown():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=True,
                      is_ask_help=True, back_kill_time=1000, server_time=10000), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE and a.uid == 42

# --- 陷阱（覆寫：只求助，絕不掙扎）---
def test_trap_challenge_always_asks_help_never_struggles():
    a = decide(_state(event_id=6001, event_type=C.TRAP, event_uid=7, is_challenge=True), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ASK_HELP

def test_trap_non_challenge_advances():
    a = decide(_state(event_id=6001, event_type=C.TRAP, event_uid=7, is_challenge=False), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

# --- 寶箱 ---
def test_box_with_key_advances():
    a = decide(_state(event_id=7001, event_type=C.BOX, bag={1520: 2}), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

def test_box_without_key_detours():
    a = decide(_state(event_id=7001, event_type=C.BOX, bag={}), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_DETOUR

# --- 遺跡 ---
def test_cave_advances():
    a = decide(_state(event_id=8001, event_type=C.CAVE), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

# --- 協助 / 領獎（優先於進層）---
def test_assist_teammate_when_help_hp_available():
    from dragon_realm.state import DragonEvent
    teammate = DragonEvent(888, 5002, C.PVE, 2, 0, is_mine=False)
    a = decide(_state(help_hp=10, event_list=(teammate,)), Cfg, PREFS)
    assert a.kind == C.A_PROVIDE_HELP and a.role_id == 888 and a.event_id == 5002

def test_assist_skipped_when_no_help_hp():
    from dragon_realm.state import DragonEvent
    teammate = DragonEvent(888, 5002, C.PVE, 2, 0, is_mine=False)
    a = decide(_state(help_hp=0, hp=30, event_list=(teammate,)), Cfg, PREFS)
    assert a.kind == C.A_EXPLORE

def test_receive_help_reward_when_available():
    a = decide(_state(help_events=(321,)), Cfg, PREFS)
    assert a.kind == C.A_RECEIVE_HELP and a.event_id == 321

def test_rekill_from_list_takes_priority():
    from dragon_realm.state import DragonEvent
    mine = DragonEvent(777, 5001, C.PVE, 9, back_kill_time=1000, is_mine=True)
    a = decide(_state(server_time=10000, event_list=(mine,)), Cfg, PREFS)  # cooldown 過
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE and a.uid == 9

# --- 活動關閉 ---
def test_activity_closed_stops():
    assert decide(_state(activity_open=False), Cfg, PREFS).kind == C.A_STOP
