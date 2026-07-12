"""check_ocr_server 快取 + last-good 優先：修 dashboard 每 2 秒輪詢被死掉的
主 OCR server timeout 拖慢 2 秒的問題（/api/status 卡頓根因）。"""
import control_panel.routes_status as rs


class _Resp:
    status_code = 200


def _reset_cache(monkeypatch):
    monkeypatch.setattr(rs, "_OCR_HEALTH_CACHE", {"ok": False, "expires_at": 0.0})
    monkeypatch.setattr(rs, "_OCR_LAST_GOOD", {"base": None})


def _fake_get(calls, good_bases):
    def get(url, timeout=None):
        calls.append((url, timeout))
        if any(url.startswith(b) for b in good_bases):
            return _Resp()
        raise ConnectionError(url)
    return get


def test_result_cached_within_ttl(monkeypatch):
    _reset_cache(monkeypatch)
    calls = []
    monkeypatch.setattr(rs.requests, "get", _fake_get(calls, ["http://100.64.0.7:5001"]))
    assert rs.check_ocr_server() is True
    n = len(calls)
    assert n >= 1
    # 第二次呼叫走快取，不再發出任何探測
    assert rs.check_ocr_server() is True
    assert len(calls) == n


def test_last_good_server_probed_first(monkeypatch):
    _reset_cache(monkeypatch)
    calls = []
    monkeypatch.setattr(rs.requests, "get", _fake_get(calls, ["http://100.64.0.7:5001"]))
    assert rs.check_ocr_server() is True
    # 讓快取過期後重測：上次成功的 server 要排第一，死掉的主 server 不再擋路
    monkeypatch.setattr(rs, "_OCR_HEALTH_CACHE", {"ok": False, "expires_at": 0.0})
    calls.clear()
    assert rs.check_ocr_server() is True
    assert calls[0][0].startswith("http://100.64.0.7:5001")
    assert len(calls) == 1


def test_health_probe_timeout_is_short(monkeypatch):
    _reset_cache(monkeypatch)
    calls = []
    monkeypatch.setattr(rs.requests, "get", _fake_get(calls, ["http://127.0.0.1:5001"]))
    rs.check_ocr_server()
    assert all(t is not None and t <= 0.5 for _, t in calls)
