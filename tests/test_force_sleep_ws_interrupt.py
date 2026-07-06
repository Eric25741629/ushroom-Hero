"""強制休眠必須即時中斷 WS 執行路徑（dashboard 按鈕 → 任務邊界 abort）。

歷史死角：Playwright 路徑每個裝置動作前有 `_pause_guard` 會 raise
ForceSleepRequested，但 WS 路徑（純 WS loop 的 run_ws_device_cycle、
WS-first 階段的 run_ws_phase）跑任務期間完全不看 FORCE_SLEEP 信號，
按了強制休眠要等整輪 WS 跑完才生效 → 使用者觀感「沒反應」。

修補的三個接點：
  1. bot_state.has_pending_force_sleep — 非消費 peek。should_abort 只能 peek，
     信號留給外層迴圈消費並轉成 force_sleep 睡眠語意（reason/policy 正確）。
  2. ws_runner_service.run_ws_device_cycle — 把 should_abort 傳進 run_device
     （任務邊界輪詢），run_device 返回後消費信號 → raise ForceSleepRequested，
     由 run_ws_device_loop 既有的 except 轉成強制休眠。
  3. ws_phase._should_abort — force_sleep pending 也中斷（任何後端，不限
     web_h5 的開瀏覽器請求）；中斷後 ledger 照寫，下輪續做。
"""
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("cv2", types.SimpleNamespace())

import bot_state  # noqa: E402
import config_manager  # noqa: E402
from game_actions import ws_phase  # noqa: E402
from ws_token.runner import RunReport  # noqa: E402

NOW = 1_700_000_000.0


class _NullLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


@pytest.fixture
def dev(request):
    """Per-test isolated device id; consume any leftover signal on teardown."""
    ip = f"fs-{request.node.name[:24]}"
    bot_state.init_device(ip)
    yield ip
    bot_state.check_force_sleep(ip)


# --- 1. bot_state peek ------------------------------------------------------

def test_has_pending_force_sleep_peeks_without_consuming(dev):
    assert bot_state.has_pending_force_sleep(dev) is False
    bot_state.request_force_sleep(dev)
    assert bot_state.has_pending_force_sleep(dev) is True
    assert bot_state.has_pending_force_sleep(dev) is True   # peek 不消費
    assert bot_state.check_force_sleep(dev) is True          # 消費
    assert bot_state.has_pending_force_sleep(dev) is False


# --- 2. run_ws_device_cycle -------------------------------------------------

@pytest.fixture
def svc_with_fake(monkeypatch):
    import runtime_services.ws_runner_service as svc

    captured = {}

    def fake_run_device(ip, **kwargs):
        captured.update(kwargs)
        hook = captured.pop("_during_run", None)
        if hook:
            hook()
        return types.SimpleNamespace(device=ip, login_ok=True, spend=False,
                                     tasks={"main_tasks": {}}, errors={})

    monkeypatch.setattr(svc, "_load_run_device", lambda: fake_run_device)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    return svc, captured


def test_cycle_passes_force_sleep_should_abort(svc_with_fake, dev):
    svc, captured = svc_with_fake
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})

    svc.run_ws_device_cycle(dev, cfg, _NullLogger())

    sa = captured.get("should_abort")
    assert callable(sa)
    assert sa() is False
    bot_state.request_force_sleep(dev)
    assert sa() is True
    # peek 語意：should_abort 不消費，信號留給外層 loop
    assert bot_state.has_pending_force_sleep(dev) is True


def test_cycle_raises_force_sleep_after_run(svc_with_fake, dev):
    """run 期間收到強制休眠 → cycle 結束時消費信號並 raise，讓 loop 進強制休眠。"""
    from runtime_services.device_runtime_service import ForceSleepRequested
    svc, captured = svc_with_fake
    captured["_during_run"] = lambda: bot_state.request_force_sleep(dev)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})

    with pytest.raises(ForceSleepRequested):
        svc.run_ws_device_cycle(dev, cfg, _NullLogger())

    assert bot_state.has_pending_force_sleep(dev) is False   # 已消費


def test_cycle_without_signal_returns_report(svc_with_fake, dev):
    svc, _ = svc_with_fake
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    report = svc.run_ws_device_cycle(dev, cfg, _NullLogger())
    assert report is not None and report.login_ok is True


# --- 3. ws_phase._should_abort ----------------------------------------------

def _cfg(monkeypatch, ws, *, backend="adb"):
    merged = {"bootstrap_token": False}
    merged.update(ws)
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: type("C", (), {"get": lambda self, k, d=None:
                                  {"ws_token": merged, "backend": backend}.get(k, d)})())


def _report(tasks, aborted=False):
    return RunReport(device="dev", login_ok=True, spend=False,
                     tasks=tasks, errors={}, aborted=aborted)


@pytest.mark.parametrize("backend", ["adb", "web_h5"])
def test_ws_phase_should_abort_on_force_sleep(tmp_path, monkeypatch, dev, backend):
    _cfg(monkeypatch, {"enabled": True}, backend=backend)
    captured = {}

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        captured["sa"] = should_abort
        return _report({"main_tasks": {}})

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    ws_phase.run_ws_phase(dev, logger_obj=_NullLogger(), now=NOW,
                          state_dir=tmp_path)

    sa = captured["sa"]
    assert sa() is False
    bot_state.request_force_sleep(dev)
    assert sa() is True                                       # 任何後端都中斷
    assert bot_state.has_pending_force_sleep(dev) is True     # 不消費
