"""Contract tests for the Phase 2 shared design-system library.

These are pure string-grep / file-existence checks (no Flask app, no device /
cv2 / Playwright imports) matching the repo's template-test convention
(see test_dashboard_template.py). They pin the *contract* between the wiring
done in this worktree (`_assets_head.html`, the 6 template includes, the
/static/ cache exemption, the version-tracking) and the shared lib files
(`static/lib/{tokens.css,components.css,app.js}`) that the design-system
refactor delivers.

If a lib file has not been written yet by a sibling builder, the lib-content
tests below will fail; that is expected — the review stage re-runs this suite
once all builders' outputs are merged.
"""
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LIB = _ROOT / "static" / "lib"
_TEMPLATES = _ROOT / "templates"

_TOKENS = _LIB / "tokens.css"
_COMPONENTS = _LIB / "components.css"
_APP_JS = _LIB / "app.js"
_HEAD = _TEMPLATES / "_assets_head.html"

# All templates that must load the lib alongside their legacy inline CSS/JS.
_ALL_TEMPLATES = [
    "dashboard.html",
    "fly_pet.html",
    "inventory.html",
    "tools_optimize.html",
    "readme_viewer.html",
    "fly_pet_login.html",
]


def _read(path: Path) -> str:
    # utf-8-sig: many files in this repo carry a UTF-8 BOM.
    return path.read_text(encoding="utf-8-sig")


# --- 1. lib files exist -----------------------------------------------------

@pytest.mark.parametrize("path", [_TOKENS, _COMPONENTS, _APP_JS])
def test_lib_file_exists(path: Path):
    assert path.is_file(), f"missing lib file: {path}"


# --- 2. tokens.css: new tokens + back-compat aliases ------------------------

def test_tokens_has_accent_and_status_text():
    css = _read(_TOKENS)
    assert "--color-accent" in css
    assert "--status-ok-text" in css


def test_tokens_keeps_back_compat_bg_alias():
    # Inline CSS/JS in the legacy templates still reference the OLD var names;
    # tokens.css must keep them resolving (aliased) so the running pages don't
    # break while inline styles co-exist with the lib.
    css = _read(_TOKENS)
    assert "--bg" in css


# --- 3. components.css: required component classes + a11y --------------------

@pytest.mark.parametrize(
    "symbol",
    [
        ".btn",
        ".modal-overlay",
        ".empty-state",
        ":focus-visible",
        ".skeleton",
    ],
)
def test_components_has_symbol(symbol: str):
    css = _read(_COMPONENTS)
    assert symbol in css, f"components.css missing {symbol!r}"


# --- 4. app.js: required JS API ---------------------------------------------

@pytest.mark.parametrize(
    "symbol",
    [
        "openModal",
        "confirmDialog",
        "keyboardable",
        "aria-live",
    ],
)
def test_app_js_has_symbol(symbol: str):
    js = _read(_APP_JS)
    assert symbol in js, f"app.js missing {symbol!r}"


def test_app_js_defines_api_post():
    js = _read(_APP_JS)
    # accept either a function declaration or an assignment form
    assert ("function apiPost" in js) or ("apiPost =" in js), "app.js missing apiPost"


# --- 5. _assets_head.html links all 3 lib files with cache-bust -------------

def test_head_partial_exists():
    assert _HEAD.is_file()


@pytest.mark.parametrize(
    "href",
    [
        "/static/lib/tokens.css?v=",
        "/static/lib/components.css?v=",
        "/static/lib/app.js?v=",
    ],
)
def test_head_links_lib_with_version_bust(href: str):
    head = _read(_HEAD)
    assert href in head, f"_assets_head.html missing versioned link {href!r}"


def test_head_loads_fonts_and_defers_app_js():
    head = _read(_HEAD)
    assert "fonts.googleapis.com" in head
    assert "Sora" in head and "Manrope" in head
    assert "<script defer" in head


# --- 6. all 6 templates include the head partial ----------------------------

@pytest.mark.parametrize("name", _ALL_TEMPLATES)
def test_template_includes_assets_head(name: str):
    html = _read(_TEMPLATES / name)
    assert "{% include '_assets_head.html' %}" in html, (
        f"{name} does not include _assets_head.html"
    )


# --- 7. control_panel_app exempts /static/ from no-store --------------------

def test_app_exempts_static_from_no_store():
    src = _read(_ROOT / "control_panel_app.py")
    # the no-store after_request must special-case /static/ and cache it
    assert 'request.path.startswith("/static/")' in src
    assert "immutable" in src
