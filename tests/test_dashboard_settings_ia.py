"""裝置設定 IA 契約：命名分層 + 單 DOM 面板 + lib 按鈕 + toast。

純字串檢查，不載入 Flask / 裝置 / OCR 依賴。
"""
from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parents[1]
_DASH = _ROOT / "templates" / "dashboard.html"


def _read() -> str:
    return _DASH.read_text(encoding="utf-8-sig")


def test_settings_naming_layers():
    html = _read()
    assert "裝置設定" in html
    assert "任務參數 — 各任務細項" in html
    assert "進階設定 — 各任務細項" not in html
    assert 'aria-label="任務參數分類"' in html
    assert "開發者選項" in html
    assert ">進階設定<" not in html


def test_task_settings_is_single_dom_panel():
    html = _read()
    # 任務參數嵌在 configModal 內，不再是獨立 overlay
    assert 'id="taskSettingsPanel"' in html
    assert 'id="configMainView"' in html
    assert 'id="taskSettingsModal"' not in html
    assert "class=\"modal-overlay\"" in html  # config/ocr 等仍用 overlay
    assert "function openTaskSettings()" in html
    assert "panel.classList.add('is-open')" in html
    assert "main.classList.add('is-hidden')" in html
    assert "返回裝置設定" in html


def test_settings_modal_uses_bem_buttons():
    html = _read()
    assert 'btn btn--primary btn-save" onclick="saveConfig()"' in html
    assert 'btn btn--ghost btn-cancel" onclick="closeModal()"' in html
    assert 'btn btn--secondary btn-skip" type="button" onclick="closeTaskSettings()"' in html


def test_card_and_toolbar_buttons_dual_mount():
    html = _read()
    # 不再有裸 legacy class（必須雙掛 lib BEM）
    assert not re.search(r'class="btn btn-skip(?![^"]*btn--)', html)
    assert not re.search(r'class="btn btn-save(?![^"]*btn--)', html)
    assert not re.search(r'class="btn btn-cancel(?![^"]*btn--)', html)
    assert "btn btn--secondary btn-skip" in html
    assert "btn btn--primary btn-save" in html
    assert "btn btn--ghost btn-cancel" in html


def test_no_runtime_alert_calls():
    html = _read()
    for line in html.splitlines():
        s = line.strip()
        if s.startswith("//"):
            continue
        if "migrations" in s or "alert()→" in s or "alert()->" in s:
            continue
        assert "alert(" not in s, f"runtime alert left: {s[:120]}"


def test_settings_feedback_uses_toast():
    html = _read()
    assert "window.UI.toast('讀取裝置設定失敗:" in html
    assert "window.UI.toast('儲存裝置設定失敗:" in html
    assert "window.UI.toast('裝置設定已儲存'" in html
    assert "window.UI.toast('OCR 設定已儲存'" in html


def test_task_tab_count_helper_present():
    html = _read()
    assert "function updateTaskTabCounts()" in html
    assert "task-tab-count" in html
    assert "settings-crumb" in html
