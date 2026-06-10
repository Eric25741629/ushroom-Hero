"""
UI 控制器 - 處理遊戲介面操作

包含點擊、像素比對、頁面判斷等功能
"""

import inspect
import time
import numpy as np
from typing import Optional, Tuple
from .config import OpenGoldConfig
from sea_v2.navigator import world_to_pixel


class UIController:
    """遊戲 UI 控制器"""

    def __init__(self, device, config: Optional[OpenGoldConfig] = None):
        self.device = device
        self.config = config or OpenGoldConfig()

    def _log_click(self, x: int, y: int, wait_time: float, reason: str) -> None:
        """印出每次 click 的座標／原因，方便人工核對是否該點。"""
        print(f"[CLICK] ({x:3d},{y:3d}) wait={wait_time}s  reason={reason}", flush=True)

    def click_and_wait(self, x: int, y: int, wait_time: float = 1.0, reason: Optional[str] = None):
        """點擊並等待。每個點擊都要記錄『為什麼點這裡』。

        呼叫端應明確傳 reason 說明這一下的用途；未傳時退回呼叫者函式名(粗略)。
        """
        if reason is None:
            try:
                reason = inspect.currentframe().f_back.f_code.co_name
            except Exception:
                reason = "?"
        self._log_click(x, y, wait_time, reason)
        self.device.click(x, y)
        time.sleep(wait_time)
    
    def capture_screenshot(self) -> np.ndarray:
        """截取畫面"""
        if hasattr(self.device, 'screenshot'):
            return self.device.screenshot(format='opencv')
        elif hasattr(self.device, 'capture_screenshot'):
            return self.device.capture_screenshot()
        else:
            raise AttributeError("裝置不支援 screenshot 或 capture_screenshot")
    
    def _pixel_sum_close(
        self, 
        img: np.ndarray, 
        x: int, 
        y: int, 
        expected_bgr: Tuple[int, int, int]
    ) -> bool:
        """檢查像素值是否接近預期值"""
        actual = img[y, x]
        return abs(int(np.sum(actual)) - int(np.sum(expected_bgr))) <= self.config.lamp_pixel_sum_tolerance
    
    def _match_pixel_profile(self, img: np.ndarray, pixel_profile) -> bool:
        """比對像素配置檔"""
        return all(
            self._pixel_sum_close(img, x, y, expected_bgr)
            for (x, y), expected_bgr in pixel_profile
        )
    
    _NODE_ACTIVE_JS = r"""(p) => {
      try {
        const sc = cc.director.getScene();
        if (!sc) return null;
        let cur = null; const parts = p.split('/');
        for (const r of sc.children) { if (r.name === parts[0]) { cur = r; break; } }
        for (let i = 1; i < parts.length && cur; i++) {
          cur = (cur.children || []).find(c => c.name === parts[i]);
        }
        if (!cur) return false;
        return !!cur.activeInHierarchy;
      } catch (e) { return null; }
    }"""

    def _cocos_node_active(self, path: str) -> Optional[bool]:
        """H5 專用：用 cocos 場景樹查 node 是否 activeInHierarchy。

        回傳 True/False；非 web_h5（無 _page）或查詢失敗回 None，呼叫者據此 fallback。
        這是 H5 取得精確遊戲狀態的首選，勝過 pixel/OCR 推測。
        """
        page = getattr(self.device, "_page", None)
        if page is None:
            return None
        try:
            return page.evaluate(self._NODE_ACTIVE_JS, path)
        except Exception:
            return None

    # 全部出售 grid 在 cocos 對應的 view（校準自 7fe98fc6 場景樹）
    SELL_ALL_NODE = "UIRoot/NormalView/EquipTempBagView"

    def is_lamp_sell_page(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷是否為全部出售頁面（20 件待賣 grid）。

        H5：精確讀 cocos `EquipTempBagView` 是否 active（已 live 驗證）。
        ADB / cocos 查詢失敗：退回原本的像素特徵比對。
        """
        active = self._cocos_node_active(self.SELL_ALL_NODE)
        if active is not None:
            return bool(active)

        if img is None:
            img = self.capture_screenshot()
        return any(
            self._match_pixel_profile(img, profile)
            for profile in self.config.lamp_sell_page_profiles
        )
    
    def is_lamp_ready_page(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷是否為神燈就緒頁面"""
        if img is None:
            img = self.capture_screenshot()
        
        return any(
            self._match_pixel_profile(img, profile)
            for profile in self.config.lamp_ready_page_profiles
        )
    
    def has_confirm_dialog(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷是否有確認對話框"""
        if img is None:
            img = self.capture_screenshot()
        
        # 檢查確認按鈕的像素特徵
        pixels = self.config.confirm_button_pixels
        return all(
            self._pixel_sum_close(img, x, y, color)
            for (x, y), color in pixels
        )
    
    def click_confirm_if_needed(self) -> bool:
        """若有確認對話框，點擊確認"""
        if self.has_confirm_dialog():
            self.click_and_wait(204, 552, 1)
            return True
        return False
    
    def is_comparison_panel_visible(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷比較面板是否已顯示（開燈成功）"""
        if img is None:
            img = self.capture_screenshot()
        pixels = self.config.comparison_panel_pixels
        return all(
            self._pixel_sum_close(img, x, y, color)
            for (x, y), color in pixels
        )

    def _got_item_blue_pct(self, img: np.ndarray) -> float:
        """中下 ROI 內落在「淺藍色」HSV 範圍的像素比例。"""
        import cv2
        y1, y2, x1, x2 = self.config.got_item_popup_roi
        roi = img[y1:y2, x1:x2]
        if roi.size == 0:
            return 0.0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        lo = np.array(self.config.got_item_popup_hsv_lo, dtype=np.uint8)
        hi = np.array(self.config.got_item_popup_hsv_hi, dtype=np.uint8)
        mask = cv2.inRange(hsv, lo, hi)
        return float(mask.sum()) / 255.0 / mask.size

    def is_got_item_popup(self, img: Optional[np.ndarray] = None) -> bool:
        """中下部分背景為淺藍色 → 真的開到裝備了，這時才該點中間。

        校準自 flow-2026-05-03.json：等待動畫的淺藍像素比例 ≤ 0.048，
        開到裝備時 ≈ 0.170；用 0.10 為門檻可清楚分離（≥ 2x 安全邊際）。
        """
        if img is None:
            img = self.capture_screenshot()
        return self._got_item_blue_pct(img) >= self.config.got_item_popup_min_pct

    def _has_text(self, target: str, y_range: Optional[Tuple[int, int]] = None) -> bool:
        """單張截圖 OCR 探測：`target` 字串是否出現在指定 y_range 區段。

        失敗時回 False，不寫 log。供 click_*_confirm 等動作的前置檢查使用，
        避免 click_str_by_server 在彈窗根本不在時還等 2s 後才印 WARNING。
        """
        try:
            img = self.capture_screenshot()
            if y_range:
                img = img[y_range[0]:y_range[1], :]
            from img_tools import analyze_skill_via_http
            import opencc

            result = analyze_skill_via_http(img)
            if not result or not result.get("success"):
                return False
            conv = opencc.OpenCC("s2t")
            target_t = conv.convert(target)
            for item in result.get("ocr_results") or []:
                text = item.get("text", "") if isinstance(item, dict) else ""
                if target_t in conv.convert(text):
                    return True
            return False
        except Exception:
            return False

    def click_auto_mode_button(self) -> bool:
        """點擊「自動」按鈕（OCR 字串定位）。前置 probe 不在則靜默跳過。"""
        if not self._has_text("自動", y_range=(780, 870)):
            return False
        try:
            from img_tools import click_str_by_server
            clicked = click_str_by_server(
                self.device,
                "自動",
                y_range=(780, 870),
                wait_timeout=0,
            )
            if clicked:
                print("[CLICK ] click_auto_mode_button hit=True", flush=True)
                time.sleep(2)
                return True
            return False
        except Exception as e:
            print(f"[UIController] click_auto_mode_button 失敗: {e}")
            return False

    def click_start_confirm(self) -> bool:
        """點擊「開始」確認彈窗（OCR 字串定位）。前置 probe 不在則靜默跳過。

        校準自 7fe98fc6：到神燈頁時 auto 常常已經在跑、根本沒有「開始」彈窗，
        此時不應印 [CLICK?] / 也不應呼叫底層 click_str_by_server 等 2s 才 timeout。
        """
        if not self._has_text("開始", y_range=(500, 700)):
            return False
        try:
            from img_tools import click_str_by_server
            clicked = click_str_by_server(
                self.device,
                "開始",
                y_range=(500, 700),
                wait_timeout=0,
            )
            if clicked:
                print("[CLICK ] click_start_confirm hit=True", flush=True)
                time.sleep(2)
                return True
            return False
        except Exception as e:
            print(f"[UIController] click_start_confirm 失敗: {e}")
            return False

    def navigate_to_lamp(self):
        """導航到神燈頁面，並啟用自動模式"""
        self.click_and_wait(447, 801, 2, reason="導航:點魔法熔爐入口")
        self.click_and_wait(281, 636, 1, reason="導航:開自動點燈設定窗")
        # 點擊「自動」按鈕啟用自動開裝模式
        self.click_auto_mode_button()
        # 點擊彈出的「開始」確認視窗
        self.click_start_confirm()

    def exit_lamp(self):
        """退出神燈頁面"""
        self.click_and_wait(447, 801, 2, reason="退出:開熔爐選單(準備離開)")
        self.click_and_wait(273, 560, 2, reason="退出:點關閉離開神燈頁")
    
    def click_all_sell(self) -> bool:
        """點擊全部出售（OCR 字串定位）。"""
        try:
            from img_tools import click_str_by_server
            print("[CLICK?] click_all_sell — 找 '全部出售'", flush=True)
            hit = click_str_by_server(self.device, "全部出售")
            print(f"[CLICK ] click_all_sell hit={hit}", flush=True)
            return hit
        except Exception as e:
            print(f"[UIController] 點擊全部出售失敗: {e}")
            return False
    
    def _ocr_texts(self, y1: int, y2: int, x1: int, x2: int) -> list:
        """OCR 指定 ROI，回傳文字 list；任何錯誤回 []（前置判斷用，不寫 log）。"""
        try:
            from img_tools import get_all_text
            img = self.capture_screenshot()
            roi = img[y1:y2, x1:x2]
            if roi.size == 0:
                return []
            return list(get_all_text(roi) or [])
        except Exception:
            return []

    def is_comparison_dialog(self, img: Optional[np.ndarray] = None) -> bool:
        """單件「當前裝備 vs NEW」待處理窗：底部同時有 出售 + 裝備 兩顆按鈕。

        與全部出售 grid 區分：grid 只有「全部出售」一顆（含「出售」但無獨立「裝備」）。
        故判斷：按鈕列 OCR 出現「裝備」且非全部出售頁。校準自 7fe98fc6 殘留窗。
        """
        if self.is_lamp_sell_page(img):
            return False
        texts = self._ocr_texts(775, 815, 60, 470)
        joined = "".join(texts)
        return ("裝備" in joined) and ("全部出售" not in joined)

    def is_blocking_popup(self, img: Optional[np.ndarray] = None) -> bool:
        """主頁一般彈窗（離線獎勵 / 恭喜獲得 領取）會擋住導航；用關鍵字偵測。"""
        texts = self._ocr_texts(140, 360, 70, 470)
        joined = "".join(texts)
        return any(kw in joined for kw in ("離線", "恭喜", "點擊空白", "領取獎勵"))

    def dismiss_blocking_popup(self) -> bool:
        """關閉主頁一般彈窗：有「領取」就點領取，否則點空白處關閉。回傳是否有動作。"""
        texts = self._ocr_texts(120, 760, 40, 500)
        joined = "".join(texts)
        try:
            from img_tools import click_str_by_server
            if "領取" in joined:
                print("[CLICK?] dismiss_blocking_popup — 領取", flush=True)
                click_str_by_server(self.device, "領取", wait_timeout=0)
                time.sleep(1.5)
                # 領取後常接「恭喜獲得」獎勵窗，點空白關閉
                self.click_and_wait(140, 300, 1.0)
                return True
            if any(kw in joined for kw in ("恭喜", "點擊空白", "離線")):
                print("[CLICK?] dismiss_blocking_popup — 點擊空白處關閉", flush=True)
                self.click_and_wait(140, 300, 1.0)
                return True
        except Exception as e:
            print(f"[UIController] dismiss_blocking_popup 失敗: {e}")
        return False

    def click_all_sell_and_verify(self, retries: int = 2) -> bool:
        """點全部出售 → 處理確認窗 → 驗證 grid 真的清空。回傳是否清空。

        修正舊版「點了就 return、沒驗證真的賣掉」導致 20 件卡住沒賣的問題。
        """
        from img_tools import click_str_by_server
        for attempt in range(retries + 1):
            if not self.is_lamp_sell_page():
                return True
            print(f"[CLICK?] click_all_sell_and_verify attempt={attempt}", flush=True)
            try:
                click_str_by_server(self.device, "全部出售")
            except Exception as e:
                print(f"[UIController] 全部出售 點擊失敗: {e}")
            time.sleep(1.0)
            # 可能跳出確認窗：像素法 + OCR「確定/確認」雙保險
            self.click_confirm_if_needed()
            try:
                if any("確" in t for t in self._ocr_texts(520, 600, 60, 480)):
                    for kw in ("確定", "確認"):
                        if click_str_by_server(self.device, kw, y_range=(520, 600), wait_timeout=0):
                            break
            except Exception:
                pass
            time.sleep(1.5)
            if not self.is_lamp_sell_page():
                print("[CLICK ] click_all_sell_and_verify cleared", flush=True)
                return True
        print("[UIController] 全部出售 後仍偵測到 sell page（未清空）", flush=True)
        return False

    def _read_int_from_roi(self, y1: int, y2: int, x1: int, x2: int) -> Optional[int]:
        """擷取畫面 ROI 並 OCR 解析為 int；失敗回 None。"""
        try:
            from img_tools import get_all_text

            img = self.capture_screenshot()
            roi = img[y1:y2, x1:x2]
            if roi.size == 0:
                return None
            ocr_results = get_all_text(roi) or []
            for text in ocr_results:
                num_str = ''.join(c for c in str(text) if c.isdigit())
                if num_str:
                    return int(num_str)
            return None
        except Exception as e:
            print(f"[UIController] _read_int_from_roi 失敗: {e}")
            return None

    # 神燈剩餘數量的 cocos Label（魔法熔爐 btnBox 上的數字，已 live 驗證）
    LAMP_COUNT_NODE = "UIRoot/NormalView/MainView/subRoots/boxRoot/btnBox/txtNum"

    _LABEL_TEXT_JS = r"""(p) => {
      try {
        const sc = cc.director.getScene(); if (!sc) return null;
        let cur = null; const parts = p.split('/');
        for (const r of sc.children) { if (r.name === parts[0]) { cur = r; break; } }
        for (let i = 1; i < parts.length && cur; i++) cur = (cur.children || []).find(c => c.name === parts[i]);
        if (!cur) return null;
        const l = cur.getComponent(cc.Label);
        return l ? l.string : null;
      } catch (e) { return null; }
    }"""

    def _cocos_lamp_count(self) -> Optional[int]:
        """H5：直接讀 cocos Label 取得剩餘神燈數量（瞬間、精確，且不受彈窗遮擋影響）。

        非 web_h5（無 _page）或失敗回 None，呼叫者 fallback OCR。
        """
        page = getattr(self.device, "_page", None)
        if page is None:
            return None
        try:
            s = page.evaluate(self._LABEL_TEXT_JS, self.LAMP_COUNT_NODE)
        except Exception:
            return None
        if not s:
            return None
        digits = "".join(c for c in str(s) if c.isdigit())
        return int(digits) if digits else None

    def get_gold_num(self) -> Optional[int]:
        """取得神燈剩餘數量。H5 走 cocos Label（瞬間）；ADB / 失敗退回 OCR ROI。"""
        n = self._cocos_lamp_count()
        if n is not None:
            return n
        return self._read_int_from_roi(*self.config.gold_num_roi)

    def get_remaining_lamp_count(self) -> Optional[int]:
        """讀取神燈剩餘數量；與 get_gold_num 同來源（cocos 優先），命名僅為語意明確。"""
        return self.get_gold_num()

    _LAMP_STATE_JS = r"""() => {
      try {
        const sc = cc.director.getScene(); if (!sc) return {state:'unknown', count:null};
        function bp(p){ let c=null; const a=p.split('/');
          for (const r of sc.children){ if(r.name===a[0]){c=r;break;} }
          for (let i=1;i<a.length&&c;i++) c=(c.children||[]).find(x=>x.name===a[i]);
          return c; }
        function act(p){ const n=bp(p); return !!(n&&n.activeInHierarchy); }
        function lbl(p){ const n=bp(p); if(!n) return null; const l=n.getComponent(cc.Label); return l?l.string:null; }
        const B='UIRoot/NormalView/MainView';
        const count = lbl(B+'/subRoots/boxRoot/btnBox/txtNum');
        let state='unknown';
        if (act('UIRoot/NormalView/EquipTempBagView')) state='sell_grid';
        else if (act(B+'/ArtifactView')) state='artifact';
        else if (act('UIRoot/NormalView/EquipAutoOpenView')) state='auto_config';
        else if (act('UIRoot/NormalView/EquipEditView')) state='comparison';
        else {
          const nv=bp('UIRoot/NormalView');
          const extra = nv ? nv.children.filter(c=>c.activeInHierarchy && c.name!=='MainView').map(c=>c.name) : [];
          const mv=bp(B);
          const mvx = mv ? mv.children.filter(c=>c.activeInHierarchy && /View$/.test(c.name)).map(c=>c.name) : [];
          state = (extra.length===0 && mvx.length===0) ? 'main' : ('other:'+extra.concat(mvx).join(','));
        }
        return {state, count};
      } catch (e) { return {state:'unknown', count:null}; }
    }"""

    def lamp_ui_state(self) -> Optional[dict]:
        """H5 專用：一次 cocos 查詢取得 {state, count}（快、精確）。

        state ∈ sell_grid / artifact / auto_config / main / other:<names> / unknown。
        非 web_h5 回 None，呼叫者用 OCR/pixel 偵測。
        """
        page = getattr(self.device, "_page", None)
        if page is None:
            return None
        try:
            return page.evaluate(self._LAMP_STATE_JS)
        except Exception:
            return None

    _WORLD_POS_JS = r"""(p) => {
      try {
        const sc = cc.director.getScene(); if (!sc) return null;
        let cur=null; const a=p.split('/');
        for (const r of sc.children){ if(r.name===a[0]){cur=r;break;} }
        for (let i=1;i<a.length&&cur;i++) cur=(cur.children||[]).find(x=>x.name===a[i]);
        if (!cur) return null;
        const w = cur.worldPosition || (cur.convertToWorldSpaceAR ? cur.convertToWorldSpaceAR(cc.v2(0,0)) : null);
        if (!w) return null;
        let ds = null; try { const d = cc.view.getVisibleSize(); ds = {w:d.width, h:d.height}; } catch(e){ ds = {w:720, h:1280}; }
        return {x:w.x, y:w.y, dw:ds.w, dh:ds.h};
      } catch (e) { return null; }
    }"""

    def click_cocos_node(self, path: str) -> bool:
        """H5：依 cocos node 的 worldPosition 換算 viewport 像素並精準點擊。

        UI 設計座標換算：px = wx*540/dw, py = (dh-wy)*960/dh（dw/dh 通常 720x1280）。
        非 web_h5 或找不到 node 回 False。
        """
        page = getattr(self.device, "_page", None)
        if page is None:
            return False
        try:
            wp = page.evaluate(self._WORLD_POS_JS, path)
        except Exception:
            wp = None
        if not wp:
            return False
        fpx, fpy = world_to_pixel(
            wp["x"], wp["y"], frame=(540, 960), design=(wp["dw"], wp["dh"])
        )
        px, py = round(fpx), round(fpy)
        self._log_click(px, py, 0, f"cocos:{path.split('/')[-1]}")
        self.device.click(px, py)
        time.sleep(1.0)
        return True

    ARTIFACT_CLOSE_NODE = "UIRoot/NormalView/MainView/ArtifactView/btnClose"

    def close_artifact_view(self) -> bool:
        """關閉誤入的神器頁（ArtifactView）。H5 用 btnClose；失敗退回固定座標 (270,899)。"""
        if self.click_cocos_node(self.ARTIFACT_CLOSE_NODE):
            return True
        self.click_and_wait(270, 899, 1.0, reason="關神器頁:fallback 固定座標")
        return True
    
    def click_lamp_button(self):
        """點擊開神燈按鈕（中間偏下→打開單件比較頁）"""
        self.click_and_wait(271, 576, 5, reason="開神燈:點中間偏下打開比較頁")

    def click_sell_button(self):
        """點擊出售按鈕（不需要的組合）"""
        self.click_and_wait(227, 798, 1, reason="判斷後:出售此裝備")
        self.click_confirm_if_needed()

    def click_keep_button(self):
        """點擊保留/換裝按鈕"""
        self.click_and_wait(376, 798, 0.3, reason="保留:切到換裝/保留方案")
        self.click_and_wait(227, 798, 1, reason="保留:確認換裝")
        self.click_confirm_if_needed()

    def open_stage_menu(self):
        """開啟階段選單"""
        self.click_and_wait(518, 16, 1, reason="開方案選單:點右上設定齒輪")
        self.click_and_wait(419, 720, 3, reason="開方案選單:切到方案頁籤")
        self.click_and_wait(272, 796, 1, reason="開方案選單:關閉浮窗")
        self.click_and_wait(281, 350, 2, reason="開方案選單:展開階段列表")

    def select_stage(self, index: int):
        """選擇階段"""
        if index == 0:
            self.click_and_wait(281, 350, 1, reason="選方案:第 0 個(列表頂)")
        else:
            click_y = 412 + (index - 1) * 49
            self.click_and_wait(266, click_y, 1, reason=f"選方案:第 {index} 個")

    def open_upgrade_panel(self):
        """開啟升級面板"""
        self.click_and_wait(378, 721, 1, reason="升級面板:切換按鈕")
        self.click_and_wait(268, 869, 1, reason="升級面板:關閉方案選單")
        self.click_and_wait(282, 584, 1, reason="升級面板:點開開到的裝備")

    def close_upgrade_panel(self):
        """關閉升級面板"""
        self.click_and_wait(347, 721, 1, reason="關升級面板:切換按鈕復位")
        self.click_and_wait(268, 869, 1, reason="關升級面板:關方案選單")
        self.click_and_wait(441, 805, 1, reason="關升級面板:關閉面板")
        self.click_and_wait(271, 634, 1, reason="關升級面板:回神燈主畫面")
        time.sleep(3)
    
    def get_skill_roi(self) -> np.ndarray:
        """取得技能資訊區域的圖片"""
        img = self.capture_screenshot()
        y1, y2, x1, x2 = self.config.skill_roi
        return img[y1:y2, x1:x2]
    
    def get_stage_roi(self) -> np.ndarray:
        """取得階段列表區域的圖片"""
        img = self.capture_screenshot()
        y1, y2, x1, x2 = self.config.stage_roi
        return img[y1:y2, x1:x2]
    
    def get_stage_roi_recheck(self) -> np.ndarray:
        """取得重新識別階段區域的圖片（較小範圍）"""
        img = self.capture_screenshot()
        y1, y2, x1, x2 = self.config.stage_roi_recheck
        return img[y1:y2, x1:x2]
    
    def get_rolled_rois(self) -> list:
        """取得開出詞條的 ROI 列表"""
        img = self.capture_screenshot()
        rois = []
        for y1, y2, x1, x2 in self.config.rolled_roi_coords:
            rois.append(img[y1:y2, x1:x2])
        return rois
    
    def get_original_rois(self, device_ip: str = None) -> list:
        """取得原有詞條的 ROI 列表"""
        img = self.capture_screenshot()
        coords = self.config.get_roi_for_device(device_ip)
        rois = []
        for y1, y2, x1, x2 in coords:
            rois.append(img[y1:y2, x1:x2])
        return rois
