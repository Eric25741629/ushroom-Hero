import time
import json
import logging

import img_tools

logger = logging.getLogger(__name__)

# 5558 live verified 2026-07-30：H5 RoleControl 的 plan_id。
_H5_PLAN_IDS = {
    "戰士推圖": 9,
    "騙人用": 10,
}


def switch_skill_h5(ip, skill_name="戰士推圖"):
    """透過 CDP 直呼 H5 內建 RoleControl 切整套方案，不使用 OCR/座標。

    頁面內 ``send_role_choose_plan_c2s`` 會沿用現有 H5 WebSocket 發送
    ``role.role_choose_plan_c2s``（0x032a），不建立第二條 WS session。
    """
    plan_id = _H5_PLAN_IDS.get(skill_name)
    if plan_id is None:
        logger.error("[%s] H5 方案名稱未設定 plan_id: %s", ip, skill_name)
        return False

    from control_panel.shared.cdp import _cdp_evaluate

    expression = (
        "(() => {"
        "if (!globalThis.roleControl || "
        "typeof globalThis.roleControl.send_role_choose_plan_c2s !== 'function') {"
        "return JSON.stringify({ok:false,error:'roleControl unavailable'});"
        "}"
        f"globalThis.roleControl.send_role_choose_plan_c2s({plan_id});"
        f"return JSON.stringify({{ok:true,plan_id:{plan_id}}});"
        "})()"
    )
    result, err = _cdp_evaluate(ip, expression, await_promise=False, timeout=10)
    if err:
        logger.error("[%s] H5 方案切換失敗 (%s): %s", ip, skill_name, err)
        return False
    try:
        value = (result or {}).get("result", {}).get("value")
        payload = json.loads(value)
    except (AttributeError, TypeError, ValueError):
        logger.error("[%s] H5 方案切換回傳無法解析 (%s): %r", ip, skill_name, result)
        return False
    if not payload.get("ok"):
        logger.error(
            "[%s] H5 方案切換失敗 (%s): %s",
            ip,
            skill_name,
            payload.get("error") or "unknown",
        )
        return False

    # 讓 H5 收完 0x032a ack 與裝備/技能/同伴等更新 push，再跑下一個任務。
    time.sleep(1)
    logger.info(
        "[%s] H5 原生方案切換已送出: %s (plan_id=%d)",
        ip,
        skill_name,
        plan_id,
    )
    return True

# 方案 (loadout / 行裝) switch flow. Panel is a fixed-layout 540x960 modal:
#   - loadout dropdown header  ~(267,153)   (shows the active loadout name)
#   - apply button 「切換方案」 ~(271,705)   (only present when a *different* loadout
#                                            is selected; reads 「使用中」 otherwise)
#   - close X                  ~(271,890)
# Old flow clicked 「冒險行裝」+shift to reach the dropdown, but 冒險行裝 also appears as
# a bottom tab, so OCR hit the wrong instance and the dropdown never opened (everything
# downstream then timed out). We open the dropdown by its fixed position instead.
def switch_skill(d, skill_name='戰士推圖'):
    # 1. open 方案 panel from home
    if not img_tools.click_str_by_server(d, '方案', y_range=(699, 758), wait_timeout=3):
        return False
    time.sleep(1.5)
    # 2. expand the loadout dropdown (fixed header position)
    d.click(267, 153)
    time.sleep(1.5)
    # 3. pick the target loadout from the expanded list (exclude header/tabs via y_range).
    # ponytail: list assumed to fit (~10 items, last at y~607); add scroll if it overflows.
    if not img_tools.click_str_by_server(d, skill_name, y_range=(175, 665), wait_timeout=3):
        d.click(271, 890)            # not found -> close, do NOT apply a wrong loadout
        time.sleep(1.5)
        return False
    time.sleep(1.0)
    # 4. apply. 切換方案 only shows when selection != active; absent (使用中) = already applied.
    img_tools.click_str_by_server(d, '切換方案', wait_timeout=3)
    time.sleep(1.0)
    # 5. close panel back to home
    d.click(271, 890)
    time.sleep(1.5)
    return True
