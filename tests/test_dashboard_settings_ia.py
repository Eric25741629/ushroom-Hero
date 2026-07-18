"""裝置設定 IA 契約：命名分層 + lib 按鈕 class + toast 回饋。

純字串檢查，不載入 Flask / 裝置 / OCR 依賴。
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DASH = _ROOT / "templates" / "dashboard.html"


def _read() -> str:
    return _DASH.read_text(encoding="utf-8-sig")


def test_settings_naming_layers():
    html = _read()
    # L1
    assert "裝置設定" in html
    # L2 — 不再用嵌套「進階設定 — 各任務細項」
    assert "任務參數 — 各任務細項" in html
    assert "進階設定 — 各任務細項" not in html
    assert 'aria-label="任務參數分類"' in html
    # L3
    assert "開發者選項" in html
    assert ">進階設定<" not in html


def test_settings_modal_uses_bem_buttons():
    html = _read()
    assert 'btn btn--primary btn-save" onclick="saveConfig()"' in html
    assert 'btn btn--ghost btn-cancel" onclick="closeModal()"' in html
    assert 'btn btn--primary btn-save" type="button" onclick="closeTaskSettings()"' in html
    assert "返回裝置設定" in html


def test_settings_feedback_uses_toast():
    html = _read()
    assert "window.UI.toast('讀取裝置設定失敗:" in html
    assert "window.UI.toast('儲存裝置設定失敗:" in html
    assert "window.UI.toast('裝置設定已儲存'" in html
    assert "window.UI.toast('OCR 設定已儲存'" in html
    assert "alert('讀取裝置設定失敗:" not in html
    assert "alert('儲存裝置設定失敗:" not in html


def test_task_tab_count_helper_present():
    html = _read()
    assert "function updateTaskTabCounts()" in html
    assert "task-tab-count" in html
    assert "settings-crumb" in html
