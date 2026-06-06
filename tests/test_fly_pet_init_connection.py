"""飛寵頁面不預設瀏覽器已開：開頁靜默偵測連線，未連線時溫和提示而非紅色錯誤。

行為契約（使用者選定）:
  - 開頁(DOMContentLoaded) 不再無條件呼叫 doLoad()，改走 initAutoLoad()。
  - initAutoLoad 先「靜默」檢查連線：能連 → doLoad 自動載入；不能連 → showNotConnectedHint，不跳 toast。
  - showNotConnectedHint：灰字提示 +『啟動瀏覽器』按鈕 highlight，不跳紅色 err toast。
  - 手動按「載入」未連線時，同樣走 showNotConnectedHint（不再紅色「遊戲未連線」err）。
"""
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"


def _t() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _fn_body(html: str, marker: str) -> str:
    start = html.index(marker)
    end = html.index("\n}\n", start)
    return html[start:end]


def test_dom_ready_uses_silent_autoload_not_blind_doload():
    """開頁不可無條件 doLoad()（那會預設瀏覽器已開並跳未連線錯誤）。"""
    html = _t()
    tail = html[html.index("DOMContentLoaded"):]
    listener = tail[: tail.index("});") + 3]
    assert "initAutoLoad()" in listener
    assert "doLoad();" not in listener


def test_init_autoload_uses_lightweight_browser_check():
    """initAutoLoad 先用輕量 checkBrowserUp(問中控台有無瀏覽器)，而非注入遊戲 JS。"""
    html = _t()
    assert "async function initAutoLoad" in html
    body = _fn_body(html, "async function initAutoLoad")
    assert "checkBrowserUp(" in body
    assert "doLoad(" in body                 # 有瀏覽器 → 載入
    assert "showNotConnectedHint" in body    # 沒瀏覽器 → 安靜提示
    assert "'err'" not in body               # 靜默：不跳任何錯誤 toast


def test_checkBrowserUp_hits_lightweight_browser_status_endpoint():
    """checkBrowserUp 走輕量 /api/fly_pet_browser_status，不注入遊戲 JS。"""
    html = _t()
    assert "async function checkBrowserUp" in html
    body = _fn_body(html, "async function checkBrowserUp")
    assert "/api/fly_pet_browser_status/" in body


def test_doload_gates_on_lightweight_browser_check():
    """doLoad 以輕量 checkBrowserUp 當門檻，沒開瀏覽器立即溫和提示(不等重探)。"""
    html = _t()
    load = _fn_body(html, "async function doLoad")
    assert "checkBrowserUp(" in load
    assert "showNotConnectedHint(" in load


def test_show_not_connected_hint_is_gentle():
    """未連線提示：灰字 + highlight 啟動按鈕，不跳紅色 err。"""
    html = _t()
    assert "function showNotConnectedHint" in html
    body = _fn_body(html, "function showNotConnectedHint")
    assert "啟動瀏覽器" in body
    assert "btn-attention" in body           # highlight 啟動按鈕
    assert "'err'" not in body               # 不跳紅色錯誤


def test_doload_disconnected_no_longer_red_errors():
    """doLoad 未連線分支改用溫和提示，移除紅色「遊戲未連線」err toast。"""
    html = _t()
    assert "toast('遊戲未連線" not in html
    load = _fn_body(html, "async function doLoad")
    # 沒開瀏覽器 → 'launch' 提示; 瀏覽器已開但遊戲載入中 → 'loading' 提示
    assert "showNotConnectedHint('launch')" in load
    assert "showNotConnectedHint('loading')" in load


def test_btn_attention_style_exists():
    """有 .btn-attention 樣式可 highlight 啟動瀏覽器按鈕。"""
    html = _t()
    assert ".btn-attention" in html
