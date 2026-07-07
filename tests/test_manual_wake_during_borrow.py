"""手動喚醒必須能打斷借用型 owner（online-monitor 等）造成的休眠暫停。

借用期間裝置 thread 阻塞在 check_pause（device_runtime_service 睡眠迴圈
:144），跑不到 skip-sleep / wake-override 的消費點；唯一逃生路徑是跨模組
契約：

  set_wake_override / set_skip_sleep（任務=休眠中時）把 next_wake_at 改成現在
  → monitor 每輪 poll 的 _about_to_wake 閘門（<=120s）成立 → 讓位 release
  → registry set_pause(False) → check_pause 解除 → 睡眠迴圈消費 override 喚醒

這裡分別釘住生產者半邊（bot_state 把 next_wake_at 拉到現在）與黏合層
（monitor 看到後放棄 sticky detector）。斷了任何一環，借用期間按「喚醒/
跳過睡眠」會無聲卡到原定喚醒前 120 秒（_HANDOFF_LEAD_SEC）才被放行。
monitor 端的 about_to_wake 排除本身已由 tests/test_online_monitor.py 釘住。
"""
import time

import bot_state
from ws_token import online_monitor as om


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._wake_overrides.pop(ip, None)
        bot_state._locks.pop(ip, None)


def _borrowed_sleeping_device(ip: str, wake_in_sec: float = 7200.0) -> None:
    """裝置正在休眠（next_wake_at 在遠處）且被借用型 owner 暫停。"""
    bot_state.init_device(ip)
    bot_state.update_state(
        ip, task="休眠中", step="常規對齊喚醒",
        next_wake_at=time.time() + wake_in_sec,
    )
    bot_state.set_pause(ip, True)  # 借用型 acquire 的效果（registry _safe_set_pause）


def test_wake_override_flips_next_wake_at_while_borrowed():
    ip = "test-borrow-wake-override"
    _cleanup(ip)
    try:
        _borrowed_sleeping_device(ip)
        before = time.time()
        bot_state.set_wake_override(ip, 0)
        nwa = bot_state.get_all_states()[ip]["next_wake_at"]
        assert before <= nwa <= time.time() + 1.0
    finally:
        _cleanup(ip)


def test_skip_sleep_flips_next_wake_at_while_borrowed():
    ip = "test-borrow-skip-sleep"
    _cleanup(ip)
    try:
        _borrowed_sleeping_device(ip)
        before = time.time()
        bot_state.set_skip_sleep(ip)
        nwa = bot_state.get_all_states()[ip]["next_wake_at"]
        assert before <= nwa <= time.time() + 1.0
    finally:
        _cleanup(ip)


class _FakeCreds:
    role_id = 1


def test_monitor_hands_off_borrowed_detector_after_manual_wake(monkeypatch):
    """黏合層：借用中按「喚醒」後，monitor 下一輪選擇必須放棄 sticky detector。"""
    ip = "test-borrow-detector-handoff"
    _cleanup(ip)
    try:
        _borrowed_sleeping_device(ip)
        monkeypatch.setattr(om, "load_creds", lambda dev: _FakeCreds())
        mon = om.OnlineMonitor(preferred=ip)
        mon._role_map = {1: ip}

        # 尚未按喚醒：休眠中 + 喚醒還很遠 → sticky 維持借用。
        assert mon._select_detector(current=ip, snapshot=None) == ip

        bot_state.set_wake_override(ip, 0)

        # 按喚醒後：next_wake_at 進入 lead 窗 → 不得再回傳 current（讓位）。
        assert mon._select_detector(current=ip, snapshot=None) != ip
    finally:
        _cleanup(ip)
