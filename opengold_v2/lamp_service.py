"""
神燈服務 - 主流程協調器

整合所有組件，提供完整的開神燈流程
"""

import time
from typing import Optional, List, Tuple, Callable
import numpy as np

from utils.logging_utils import logger

from .config import OpenGoldConfig
from .lamp_loop_state import LampExhaustionTracker, LampLoopAction, LampLoopState
from .models import Equipment, LampState, ComparisonResult
from .ocr_parser import OCRParser
from .skill_evaluator import SkillEvaluator
from .screenshot_logger import ScreenshotLogger
from .device_detector import DeviceDetector
from .ui_controller import UIController


class LampService:
    """神燈開裝備服務"""

    def __init__(
        self,
        device,
        config: Optional[OpenGoldConfig] = None,
        analyze_skill_fn: Optional[Callable] = None,
        analyze_stage_fn: Optional[Callable] = None,
        device_ip: Optional[str] = None,
        loop_state: Optional[LampLoopState] = None,
    ):
        self.device = device
        self.config = config or OpenGoldConfig()
        self.device_ip = device_ip

        self.ui = UIController(device, self.config)
        self.parser = OCRParser(self.config)
        self.screenshot_logger = ScreenshotLogger(self.config, device_ip=device_ip)
        self.device_detector = DeviceDetector(self.config)

        if analyze_skill_fn is None:
            from img_tools import analyze_skill_via_http
            self.analyze_skill_fn = analyze_skill_via_http
        else:
            self.analyze_skill_fn = analyze_skill_fn

        # Fix: was incorrectly assigned `analyze_stage_fn` (None) instead of the imported function
        if analyze_stage_fn is None:
            from img_tools import analyze_stage_via_server
            self.analyze_stage_fn = analyze_stage_via_server
        else:
            self.analyze_stage_fn = analyze_stage_fn

        self.state = LampState()
        self.has_lian_shan_equip: Optional[bool] = None
        self.loop_state = loop_state or self._make_default_loop_state()

    @staticmethod
    def _make_default_loop_state() -> LampLoopState:
        """Defaults pinned to the user's "40s = stuck" criterion at ~2s tick.

        - stable_threshold=2: a popup needs only ~4s of count stability before
          we trust HANDLE_POPUP — popup detection is the real gate.
        - reengage_after_stable=20: 40s of static count + no popup → reengage
          auto mode (in case in-game auto silently paused).
        - unreadable_renav_threshold=20: 40s of unreadable count → renavigate
          (we likely drifted off the lamp page).
        """
        return LampLoopState(
            stable_threshold=2,
            reengage_after_stable=20,
            unreadable_renav_threshold=20,
        )

    def _read_pairs_from_rois(self, rois: List[np.ndarray]) -> List[Tuple[str, Optional[float]]]:
        """從多個 ROI 讀取 (skill_code, prob) 列表"""
        pairs = []
        for roi in rois:
            try:
                res = self.analyze_skill_fn(roi)
                txt = self.parser.get_first_text_from_skill_result(res)
                skill_code, prob = self.parser.parse_skill_prob(txt)
                pairs.append((skill_code, prob))
                logger.info(f"[LampService] OCR 結果: '{txt}' -> ({skill_code}, {prob})")
            except Exception as e:
                logger.warning(f"[LampService] ROI 讀取失敗: {e}")
                pairs.append((None, None))
        return pairs

    @staticmethod
    def _is_pairs_incomplete(pairs: List[Tuple]) -> bool:
        """True iff `pairs` is missing entries or has any None / empty skill code.

        No `self` use → staticmethod (was instance method only by accident).
        """
        if not isinstance(pairs, (list, tuple)):
            return True
        if len(pairs) < 2:
            return True
        for p in pairs:
            if not p or len(p) < 1:
                return True
            skill = p[0]
            if skill is None:
                return True
            if isinstance(skill, str) and skill.strip() == "":
                return True
        return False

    @staticmethod
    def _is_prob_too_large(pairs: List[Tuple], threshold: float = 10.0) -> bool:
        """True iff any pair has a numeric prob exceeding `threshold` (OCR misread)."""
        for p in (pairs or []):
            if not p or len(p) < 2 or p[1] is None:
                continue
            try:
                if float(p[1]) > float(threshold):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _detect_lian_shan_equip(self) -> bool:
        try:
            stage_img = self.ui.get_stage_roi()
            stage_result = self.analyze_stage_fn(stage_img)
            return self.device_detector.detect_lian_shan_from_ocr_result(stage_result)
        except Exception as e:
            fallback = self.has_lian_shan_equip if self.has_lian_shan_equip is not None else False
            logger.warning(f"[LampService] 偵測連閃裝備失敗: {e}，使用上次結果: {fallback}")
            return fallback

    def _log_screenshot(self, prefix: str = "lamp", suffix: str = ""):
        try:
            self.screenshot_logger.save_screenshot_from_device(
                self.device, prefix=prefix, suffix=suffix
            )
        except Exception as e:
            logger.warning(f"[LampService] 截圖記錄失敗: {e}")

    def _log_roi(self, roi: np.ndarray, label: str):
        """儲存 ROI 小圖供後續調試"""
        try:
            self.screenshot_logger.save_roi(roi, prefix="roi", suffix=label)
        except Exception as e:
            logger.warning(f"[LampService] ROI 截圖記錄失敗: {e}")

    _BATCH_ANIMATION_WAIT = 10  # seconds to wait when gold count is in flight

    def _handle_sell_page_if_present(self, label: str) -> bool:
        """If on the 全部出售 page, run the bulk-sell action and return True."""
        if not self.ui.is_lamp_sell_page():
            return False
        logger.debug(f"[LampService] {label}")
        self.ui.click_all_sell()
        return True

    def _check_batch_in_flight(self) -> bool:
        """Returns True iff a lamp-batch animation is in progress (gold count
        changed since last tick). Sleeps `_BATCH_ANIMATION_WAIT`s when so."""
        gold_num = self.ui.get_gold_num()
        if gold_num is None:
            return False
        logger.info(f"[LampService] 神燈數量: {gold_num}")
        last = self.state.last_gold_num
        self.state.last_gold_num = gold_num
        if last is not None and gold_num != last:
            logger.info(f"[LampService] 神燈數量變化: {last} -> {gold_num}")
            time.sleep(self._BATCH_ANIMATION_WAIT)
            return True
        return False

    _NO_DECREASE_WARN_THRESHOLD = 3  # consecutive non-decreases before WARNING
    _REENGAGE_PROGRESS_GRACE_SEC = 60.0  # skip REENGAGE if count dropped within this many seconds

    def _record_lamp_consumption(self, before_count: Optional[int]) -> None:
        """Health check: after each lamp use, verify the count actually dropped.

        Resets stuck-streak on a real decrease; increments on no-change /
        unexpected-increase. Emits WARNING when the streak hits the threshold
        (and on every subsequent stuck round) so operators see ongoing trouble.

        OCR failure on either side is uninformative — silently skip.
        """
        after_count = self.ui.get_gold_num()
        if before_count is None or after_count is None:
            return
        if after_count < before_count:
            self.state.record_lamp_decrease()
            return
        streak = self.state.record_lamp_no_change()
        if streak >= self._NO_DECREASE_WARN_THRESHOLD:
            logger.warning(
                f"[LampService] 神燈數量連續 {streak} 次未減少 "
                f"(before={before_count}, after={after_count}) — 可能 click 沒到位 / 流程卡住"
            )

    def process_single_lamp(
        self,
        is_compare: bool = True,
        skip_batch_check: bool = False,
        packet_combo: Optional[str] = None,
    ) -> bool:
        """處理單次開神燈。回傳 True=正常完成；False=因 OCR 不完整而跳過。

        skip_batch_check=True：封包已確認開到要的品質(遊戲已停住、留 1 顆)，直接開比較頁
        判斷，不要被 _check_batch_in_flight 的「批次動畫中 → sleep 10s 後 bail」拖延
        （否則永恆要等 ~30s 才走慢路徑處理）。

        packet_combo：封包為主 — 直接給正規化 combo(由封包詞條算)，略過 skill_roi OCR。
        OCR 會把「暈」讀成簡體「晕」而漏字 → combo 出錯找不到階段；封包不會。
        """
        self._log_screenshot(prefix="lamp", suffix="start")

        if self._handle_sell_page_if_present("當前在全部出售頁面"):
            return True
        if not skip_batch_check and self._check_batch_in_flight():
            return True

        before_count = self.ui.get_gold_num()
        self.ui.click_lamp_button()
        try:
            time.sleep(1)
            self._log_screenshot(prefix="lamp", suffix="opened")

            # 鎏金批量結算可能在點擊後彈出
            if self._handle_sell_page_if_present("開燈後出現全部出售頁面，執行全部出售"):
                return True

            if packet_combo:
                combo_norm = packet_combo
                unwanted = self.parser.is_unwanted_combo(combo_norm)
                combo_msg = f"[LampService] 技能組合(封包為主): {combo_norm} ; 不要? {unwanted}"
            else:
                skill_roi = self.ui.get_skill_roi()
                self._log_roi(skill_roi, "skill_roi")
                skill_result = self.analyze_skill_fn(skill_roi)

                if not skill_result or not skill_result.get('success'):
                    logger.warning("[LampService] 技能 OCR 失敗")
                    self._log_screenshot(prefix="lamp", suffix="ocr_fail")
                    return True

                parsed = self.parser.parse_ocr(skill_result.get('ocr_results', []))
                combo_norm = parsed.combo_norm
                unwanted = parsed.unwanted
                combo_msg = (
                    f"[LampService] 技能組合: {parsed.combo_raw} => {combo_norm} ; 不要? {unwanted}"
                )
            if unwanted:
                logger.debug(combo_msg)
            else:
                logger.info(combo_msg)

            if unwanted:
                logger.debug("[LampService] 不需要的組合，執行出售")
                self._log_screenshot(prefix="lamp", suffix="sold_unwanted")
                self.ui.click_sell_button()
                self.state.record_ocr_success()
                return True

            logger.info("[LampService] 需要的組合，進入比較流程")
            skipped = self._process_wanted_combo(combo_norm, is_compare)
            return not skipped
        finally:
            self._record_lamp_consumption(before_count)

    def _process_wanted_combo(self, combo: str, is_compare: bool) -> bool:
        """
        處理需要的技能組合

        回傳:
        - True: 因 OCR 不完整而跳過
        - False: 正常處理完成
        """
        self.ui.open_stage_menu()

        # 階段3：選單打開後畫面（確認階段列表識別）
        self._log_screenshot(prefix="lamp", suffix="stage_menu")

        stage_img = self.ui.get_stage_roi()
        # 儲存階段列表 ROI
        self._log_roi(stage_img, "stage_list")

        stage_result = self.analyze_stage_fn(stage_img)

        if not stage_result:
            logger.warning("[LampService] 無法識別階段資訊")
            return False

        stage_texts = stage_result.get('stage_texts', [])
        stage_texts = [self.parser.normalize_stage_name(t) for t in stage_texts]
        stage_texts = [t for t in stage_texts if len(t) >= 2]

        logger.info(f"[LampService] 正規化後階段: {stage_texts}")

        if self.has_lian_shan_equip is None:
            self.has_lian_shan_equip = self._detect_lian_shan_equip()
            logger.info(f"[LampService] 連閃裝備偵測結果: {self.has_lian_shan_equip}")

        if combo in stage_texts:
            index = stage_texts.index(combo)
            logger.info(f"[LampService] 找到階段 '{combo}' 在索引: {index}")
            self.ui.select_stage(index)
            return self._execute_upgrade_sequence(index, stage_texts, is_compare)
        else:
            logger.warning(f"[LampService] 未找到階段 '{combo}'，可用階段: {stage_texts}")
            return False

    def _execute_upgrade_sequence(
        self,
        index: int,
        stage_texts: List[str],
        is_compare: bool
    ) -> bool:
        """
        執行升級序列

        回傳:
        - True: 因 OCR 不完整而跳過
        - False: 正常處理完成
        """
        self.ui.open_upgrade_panel()

        # 階段4：升級面板（可見 rolled + original 詞條）
        self._log_screenshot(prefix="lamp", suffix="upgrade_panel")

        rolled_rois = self.ui.get_rolled_rois()
        orig_rois_primary = self.ui.get_original_rois(self.device_ip)

        if self.device_ip and 'adb-fc65396d-4LPqmI._adb-tls-connect._tcp' in self.device_ip:
            orig_rois_secondary = self.ui.get_original_rois(None)
        else:
            orig_rois_secondary = self.ui.get_original_rois('adb-fc65396d-4LPqmI._adb-tls-connect._tcp')

        # 儲存各詞條 ROI（供座標校準用）
        for i, roi in enumerate(rolled_rois):
            self._log_roi(roi, f"rolled_{i}")
        for i, roi in enumerate(orig_rois_primary):
            self._log_roi(roi, f"orig_{i}")

        rolled_pairs = self._read_pairs_from_rois(rolled_rois)
        original_pairs = self._read_pairs_from_rois(orig_rois_primary)

        logger.debug(f"[LampService] rolled pairs: {rolled_pairs}")
        logger.debug(f"[LampService] original pairs: {original_pairs}")

        if self._is_pairs_incomplete(original_pairs):
            logger.info("[LampService] 原有詞條解析不完整，嘗試使用備用 ROI 重試...")
            alt_pairs = self._read_pairs_from_rois(orig_rois_secondary)
            if not self._is_pairs_incomplete(alt_pairs):
                original_pairs = alt_pairs
                logger.info("[LampService] 採用備用 ROI 的解析結果")

        if self._is_prob_too_large(original_pairs):
            logger.warning("[LampService] 原有詞條出現超過 10% 的機率，視為 OCR 誤判 -> 嘗試備用 ROI...")
            alt_pairs = self._read_pairs_from_rois(orig_rois_secondary)
            if not self._is_pairs_incomplete(alt_pairs) and not self._is_prob_too_large(alt_pairs):
                original_pairs = alt_pairs
                logger.info("[LampService] 採用備用 ROI 的解析結果（通過機率合理性檢查）")

        if self._is_pairs_incomplete(rolled_pairs) or self._is_pairs_incomplete(original_pairs):
            logger.warning("[LampService] OCR 無法完整辨識詞條，跳過自動比較")
            # 階段5：OCR 不完整（診斷截圖）
            self._log_screenshot(prefix="lamp", suffix="incomplete_ocr")
            return True

        rolled_equip = Equipment.from_pairs(rolled_pairs)
        original_equip = Equipment.from_pairs(original_pairs)

        evaluator = SkillEvaluator(self.config, self.has_lian_shan_equip)
        result = evaluator.compare_skill_pairs(rolled_equip, original_equip, is_compare)

        if result.should_replace:
            logger.info(f"[LampService] 比對結果: replace=True ; reason: {result.reason}")
            logger.info("[LampService] 執行換裝")
            # 階段6a：決定保留（換裝）
            self._log_screenshot(prefix="lamp", suffix="kept")
            self.ui.click_keep_button()
        else:
            logger.debug(f"[LampService] 比對結果: replace=False ; reason: {result.reason}")
            logger.debug("[LampService] 不換，執行出售")
            # 階段6b：決定出售（不換）
            self._log_screenshot(prefix="lamp", suffix="sold")
            self.ui.click_sell_button()

        self._return_to_original_equipment(stage_texts)

        return False

    def _return_to_original_equipment(self, original_stage_texts: List[str]):
        """切換回原始裝備"""
        self.ui.click_and_wait(419, 720, 3, reason="切回原裝:切到方案頁籤")
        self.ui.click_and_wait(272, 796, 1, reason="切回原裝:關閉浮窗")
        self.ui.click_and_wait(281, 350, 1, reason="切回原裝:展開階段列表")

        stage_img = self.ui.get_stage_roi_recheck()
        stage_result = self.analyze_stage_fn(stage_img)

        if stage_result and stage_result.get('success'):
            current_stage_texts = stage_result.get("stage_texts", [])
            current_stage_texts = [t for t in current_stage_texts if len(t) >= 2]

            original_combo = original_stage_texts[0]
            if original_combo in current_stage_texts:
                original_index = current_stage_texts.index(original_combo)
                logger.info(f"[LampService] 切換回原始裝備 '{original_combo}' 在索引: {original_index}")
                self.ui.select_stage(original_index)
            else:
                logger.warning(f"[LampService] 未找到原始裝備 '{original_combo}'，點擊第一個")
                self.ui.select_stage(0)
        else:
            logger.warning("[LampService] 無法重新識別階段，點擊第一個")
            self.ui.select_stage(0)

        self.ui.close_upgrade_panel()

    _AUTO_RETRY_EVERY = 2  # re-engage auto mode every Nth consecutive skip

    def _try_recover_or_stop(self) -> bool:
        """連續跳過達上限時的決策。

        若剩餘神燈 > 0，視為 OCR/UI 暫時失常 → reset 計數、重新點 auto/start，回傳 True 繼續。
        否則收尾，回傳 False。
        """
        remaining = self.ui.get_remaining_lamp_count()
        if remaining is not None and remaining > 0:
            logger.info(
                f"[LampService] 連續跳過 {self.config.skip_incomplete_limit} 次 OCR 不完整，"
                f"但神燈剩餘 {remaining} 顆，重置計數並嘗試恢復後繼續。"
            )
            self.state.record_ocr_success()
            try:
                self.ui.click_auto_mode_button()
                self.ui.click_start_confirm()
            except Exception as recover_exc:
                logger.warning(f"[LampService] 嘗試恢復自動模式失敗: {recover_exc}")
            return True
        logger.info(
            f"[LampService] 連續跳過 {self.config.skip_incomplete_limit} 次 OCR 不完整，"
            f"剩餘={remaining}，停止。"
        )
        return False

    def _record_skip_and_maybe_reengage(self) -> None:
        count = self.state.record_ocr_incomplete()
        logger.info(f"[LampService] OCR 不完整已跳過次數: {count}/{self.config.skip_incomplete_limit}")
        if count % self._AUTO_RETRY_EVERY == 0:
            logger.info("[LampService] 嘗試重新啟用自動模式...")
            self.ui.click_auto_mode_button()
            self.ui.click_start_confirm()

    def _detect_popup(self) -> bool:
        """中下部分背景為淺藍色 → 真的開到裝備了。

        校準自 flow-2026-05-03.json (節點 3 vs 節點 4)：等待動畫的淺藍像素
        比例 ≤ 0.048，開到裝備時 ≈ 0.170；採用 0.10 為門檻 (>= 2x margin)。
        以這個 opencv 檢查取代之前的 OCR-based skill_roi 檢查 — 後者在
        動畫中常出現 false positive，導致 bot 在 auto running 時誤點 (271, 576)
        中央位置打斷流程。
        """
        try:
            return self.ui.is_got_item_popup()
        except Exception as exc:
            logger.warning(f"[LampService] popup 偵測失敗: {exc}")
            return False

    def _read_lamp_count_robust(
        self,
        samples: int = 3,
        agree: int = 2,
        gap_sec: float = 0.15,
    ) -> Optional[int]:
        """Multi-frame consensus read of remaining lamp count.

        Defends against the disenchant-XP overlay momentarily covering the
        gold ROI (observed symptom: count briefly reads `2` between batches
        when an unwanted item is sold-and-XP-popped). Returns the most-common
        value if at least `agree` non-None samples match; otherwise None.
        """
        from collections import Counter

        readings = []
        for i in range(samples):
            readings.append(self.ui.get_remaining_lamp_count())
            if gap_sec > 0 and i + 1 < samples:
                time.sleep(gap_sec)
        counts = Counter(v for v in readings if v is not None)
        if not counts:
            return None
        value, n = counts.most_common(1)[0]
        return value if n >= agree else None

    def _adaptive_wait(self, lamp_count: Optional[int]) -> None:
        """Shorter waits when count keeps changing (animation hot), longer when idle."""
        if lamp_count is None:
            time.sleep(1.5)
        elif self.loop_state.stable_streak == 0:
            time.sleep(2)
        else:
            time.sleep(1)

    def _reengage_auto(self) -> None:
        """Re-trigger 自動 + 開始 buttons to resume in-game auto-open.

        Snapshots the screen before clicking so post-mortem debugging can
        check whether the device was actually on the lamp page (vs main page,
        sell-all, or a banner overlay) when the click landed.
        """
        self._log_screenshot(prefix="lamp", suffix="before_reengage")
        try:
            self.ui.click_auto_mode_button()
            self.ui.click_start_confirm()
        except Exception as exc:
            logger.warning(f"[LampService] reengage 失敗: {exc}")

    def _enter_lamp_and_clear(self, is_compare: bool) -> bool:
        """進燈介面(自動點燈設定窗)並清掉殘留，回到可按「開始」的就緒態。

        H5：用 cocos lamp_ui_state() 精確分類(快、不亂猜)；ADB：OCR/pixel fallback。
        殘留：賣場 grid→全部出售；單件/未知 modal→同一套規則(process_single_lamp)；
        誤入神器頁→關閉。回傳 True=就緒(H5:已在 auto_config 設定窗); False=清不掉。
        """
        if getattr(self.device, "_page", None) is not None:
            return self._enter_and_clear_cocos(is_compare)
        return self._enter_and_clear_adb(is_compare)

    def _enter_and_clear_cocos(self, is_compare: bool) -> bool:
        """H5：依 cocos 狀態清殘留並進入自動點燈設定窗。"""
        from .lamp_startup import StartupAction, StartupResolver, classify_entry_state

        resolver = StartupResolver(max_iters=14, max_stall=3)
        while True:
            ui = self.ui.lamp_ui_state() or {"state": "unknown"}
            st = ui.get("state") or "unknown"

            if st == "auto_config":
                logger.info("[LampService] 已在自動點燈設定窗 → 就緒")
                return True
            if st == "artifact":
                logger.warning("[LampService] 誤入神器頁(ArtifactView) → 關閉")
                self.ui.close_artifact_view()
                time.sleep(0.6)
                continue

            sell_all = (st == "sell_grid")
            comparison = (st == "comparison")          # EquipEditView 單件 → 走規則
            popup = st.startswith("other")             # 未知 modal → 嘗試關閉(不亂點)
            on_main = (st == "main")
            state = classify_entry_state(
                sell_all=sell_all, comparison=comparison, blocking_popup=popup, main=on_main
            )
            action = resolver.next_action(state)
            logger.info(f"[LampService] 清殘留(cocos): ui={st} -> {action.value} iter={resolver.iters}")

            if action == StartupAction.DONE:
                return True
            if action == StartupAction.ABORT:
                logger.warning("[LampService] 清殘留 ABORT")
                self._log_screenshot(prefix="lamp", suffix="clear_abort")
                return False
            if action == StartupAction.SELL_ALL:
                self.ui.click_all_sell_and_verify()
            elif action == StartupAction.PROCESS_OPENED_EQUIP:
                self.process_single_lamp(is_compare)
            elif action == StartupAction.ENTER_LAMP:
                self.ui.click_and_wait(447, 801, 2, reason="清殘留:點熔爐入口進設定窗")
            time.sleep(0.4)

    def _enter_and_clear_adb(self, is_compare: bool) -> bool:
        """ADB(無 cocos)：OCR/pixel 清殘留後進入設定窗。"""
        try:
            if self.ui.is_blocking_popup():
                self.ui.dismiss_blocking_popup()
        except Exception:
            pass
        self.ui.click_and_wait(447, 801, 2, reason="ADB清殘留:進自動點燈設定窗")
        for _ in range(6):
            try:
                if self.ui.is_lamp_sell_page():
                    self.ui.click_all_sell_and_verify()
                    self.ui.click_and_wait(447, 801, 2, reason="ADB清殘留:賣場後重進設定窗")
                    continue
                if self.ui.is_comparison_dialog():
                    self.process_single_lamp(is_compare)
                    self.ui.click_and_wait(447, 801, 2, reason="ADB清殘留:單件後重進設定窗")
                    continue
            except Exception as exc:
                logger.warning(f"[LampService] ADB 清殘留失敗: {exc}")
            break
        return True

    def _finish_clean(self, is_compare: bool) -> None:
        """收尾前清掉開著的 全部出售 / 單件比較窗，避免殘留到下一輪(根因防護)。"""
        for _ in range(4):
            try:
                if self.ui.is_lamp_sell_page():
                    self.ui.click_all_sell_and_verify()
                    continue
                if self.ui.is_comparison_dialog():
                    self.process_single_lamp(is_compare)
                    continue
            except Exception as exc:
                logger.warning(f"[LampService] _finish_clean 失敗: {exc}")
            break

    def run(self, times: int = 1000, is_compare: bool = True):
        """執行開神燈主流程。times=-1 表示無限。

        Outer loop is gated by LampLoopState — only triggers process_single_lamp
        on HANDLE_POPUP (count stable + popup OCR detected). All other states
        (WAIT/REENGAGE_AUTO/RENAVIGATE) handle non-popup conditions without
        clicking the equipment-confirm button.
        """
        start_time = time.time()
        if times == -1:
            times = float('inf')

        logger.info(f"[LampService] 開始開神燈，設定: times={times}, is_compare={is_compare}")
        self._last_is_compare = is_compare

        # 開燈前先讀剩餘神燈(魔法熔爐)數量 — 沒燈就不硬開(仍會清殘留)
        try:
            pre_count = self.ui.get_remaining_lamp_count()
        except Exception:
            pre_count = None
        logger.info(f"[LampService] 開燈前剩餘神燈: {pre_count}")

        # 進燈介面 + 清三種殘留狀態(全部出售 / 單件比較 / 一般彈窗) → 就緒
        if not self._enter_lamp_and_clear(is_compare):
            logger.warning("[LampService] 無法清乾淨殘留/未就緒，結束本輪")
            try:
                self._finish_clean(is_compare)
                self.ui.exit_lamp()
            except Exception as exc:
                logger.warning(f"[LampService] 收尾失敗: {exc}")
            return

        if pre_count == 0:
            logger.info("[LampService] 剩餘神燈為 0，已清乾淨殘留，不啟動自動開裝")
            try:
                self.ui.exit_lamp()
            except Exception as exc:
                logger.warning(f"[LampService] exit_lamp 失敗: {exc}")
            return

        # 就緒(自動點燈設定窗) → 按「開始」啟動連續自動開燈
        # (live 驗證: 按一次開始即連續自動開+自動賣, ~1批/秒, 不需逐件處理)
        self.ui.click_and_wait(281, 636, 2, reason="按開始:啟動連續自動開燈")
        self.state.is_running = True

        try:
            if getattr(self.device, "_page", None) is not None:
                self._run_open_loop_cocos(start_time, times, is_compare)
            else:
                self._run_open_loop_legacy(start_time, times, is_compare)
        finally:
            logger.info("[LampService] 結束開神燈流程")
            try:
                self._finish_clean(is_compare)
            except Exception as exc:
                logger.warning(f"[LampService] _finish_clean 失敗: {exc}")
            try:
                self.ui.exit_lamp()
            except Exception as exc:
                logger.warning(f"[LampService] exit_lamp 失敗: {exc}")
            self.state.is_running = False

    _STALL_RESTART_SEC = 8.0
    _EXHAUSTED_STOP_TICKS = 2  # 連續讀到剩餘神燈為 0 幾次後判定開完並停止本輪

    def _run_open_loop_cocos(self, start_time: float, times: float, is_compare: bool) -> None:
        """H5 cocos 監控迴圈(快、精確):
          - 封包為主：0x0504 出現要的品質(預設永恆) → 立刻點開判斷
          - count 下降 = 開燈中 → 等
          - count 停滯 ≥ _STALL_RESTART_SEC → 重新啟動自動開燈(備援，封包漏接時)
          - 出現賣場 grid(matching items 累積) → 全部出售
          - 誤入神器頁 → 關閉
          - 其他未知 modal → 走同一套規則處理
        """
        self._arm_wanted_capture()
        seen_uids: set = set()
        last_count: Optional[int] = None
        last_drop_ts = time.time()
        exhaustion = LampExhaustionTracker(stop_after=self._EXHAUSTED_STOP_TICKS)
        while time.time() - start_time < times and self.state.is_running:
            # 封包為主：開到要的品質(預設永恆)時遊戲會停住(賣19留1)，0x0504 一到就確定，
            # 不必等數量停滯。process_single_lamp 會點 (271,576) 開比較頁並依 combo 規則判斷賣/留。
            # 用 uid 去重：同一件掉落（可能跨 frame / 跨 tick 重複出現）只觸發一次。
            wanted = self._drain_wanted_drops()
            # 依 uid 去重：同一件掉落可能在同一次 drain 出現多個 frame，也可能跨 tick 重複。
            # 都只算一件，避免「同一件 → len(fresh)>1 → 誤退回 OCR」害封包 combo 沒用上。
            fresh = []
            batch_uids: set = set()
            for w in wanted:
                uid = w.get("uid")
                if uid in seen_uids or uid in batch_uids:
                    continue
                batch_uids.add(uid)
                fresh.append(w)
            if fresh:
                for entry in fresh:
                    seen_uids.add(entry.get("uid"))
                    self._notify_wanted_drop(entry)
                # 封包為主：1 件時用封包詞條算 combo（OCR 會把「暈」讀成簡體「晕」漏字）；
                # 多件無法確定比較頁顯示哪件 → 退回 OCR。
                packet_combo = (
                    self._combo_from_packet_entry(fresh[0]) if len(fresh) == 1 else None
                )
                # 遊戲已停住(賣19留1)。立刻開比較頁判斷，略過 batch-in-flight 守衛
                # (它會因賣19的數量變動 sleep 10s 後 bail → 害永恆等 ~30s 才被處理)。
                time.sleep(1.5)  # 等賣19動畫收尾，比較頁就緒
                self.process_single_lamp(
                    is_compare, skip_batch_check=True, packet_combo=packet_combo
                )
                last_drop_ts = time.time()
                continue

            ui = self.ui.lamp_ui_state() or {}
            st = ui.get("state") or "unknown"
            cnt_s = ui.get("count")
            cnt = None
            if cnt_s:
                digits = "".join(c for c in str(cnt_s) if c.isdigit())
                cnt = int(digits) if digits else None

            if st == "sell_grid":
                logger.debug("[LampService] 開燈中出現賣場 grid → 全部出售")
                self.ui.click_all_sell_and_verify()
                last_drop_ts = time.time()
                continue
            if st == "artifact":
                logger.warning("[LampService] 開燈中誤入神器頁 → 關閉")
                self.ui.close_artifact_view()
                continue
            if st == "comparison":
                logger.info("[LampService] 開燈中出現單件比較(EquipEditView) → 走同一套規則")
                self.process_single_lamp(is_compare)
                continue
            if st.startswith("other"):
                logger.info(f"[LampService] 開燈中未知 modal: {st} → 嘗試關閉")
                self.ui.dismiss_blocking_popup()
                continue

            # 剩餘神燈歸零 → 開燈完成，停止本輪。否則空爐會被當「停滯」一直
            # _restart_auto_open()(重按開始)空轉到 times 逾時(~300s)。
            if exhaustion.is_exhausted(cnt):
                logger.info("[LampService] 剩餘神燈為 0，開燈完成 → 停止本輪")
                break

            if cnt is not None and last_count is not None and cnt < last_count:
                last_drop_ts = time.time()
            if cnt is not None:
                last_count = cnt

            if time.time() - last_drop_ts >= self._STALL_RESTART_SEC:
                logger.info(f"[LampService] count={cnt} 停滯 ≥{self._STALL_RESTART_SEC}s → 重新啟動自動開燈")
                self._restart_auto_open()
                last_drop_ts = time.time()
            else:
                time.sleep(1.0)

    def _restart_auto_open(self) -> None:
        """count 停滯時復原：確保在自動點燈設定窗，再按「開始」。"""
        ui = self.ui.lamp_ui_state() or {}
        if (ui.get("state") or "") != "auto_config":
            self.ui.click_and_wait(447, 801, 2, reason="停滯復原:進自動點燈設定窗")
        self.ui.click_and_wait(281, 636, 2, reason="停滯復原:重按開始")

    def _wanted_rarity(self) -> int:
        """要的品質門檻（封包偵測），預設 11 永恆。"""
        return int(getattr(self.config, "wanted_rarity", 11) or 11)

    def _arm_wanted_capture(self) -> None:
        """H5：安裝 WS hook 並 arm 0x0504 body 擷取，供開燈時即時偵測要的品質。

        非 H5(無 _page)或失敗時靜默跳過 — 退回數量停滯備援。
        """
        page = getattr(self.device, "_page", None)
        if page is None:
            return
        try:
            from utils.ws_listener import (
                drain_captured_bodies,
                install_ws_listener,
                set_body_capture,
            )
            from utils.lamp_drop_watch import LAMP_DROP_CMD
            install_ws_listener(page)
            set_body_capture(page, [LAMP_DROP_CMD], max_per_cmd=100000, max_bytes=65536)
            # 清掉 arm 之前 buffer 內的舊 0x0504（上一輪殘留 / 已處理掉落），只認「開始」
            # 之後的新掉落 — 否則已賣掉的永恆舊封包會被誤判成新偵測（假觸發）。
            drain_captured_bodies(page)
        except Exception as exc:
            logger.warning(f"[LampService] arm 0x0504 擷取失敗（退回數量停滯偵測）: {exc}")

    def _drain_wanted_drops(self) -> list:
        """H5：drain 0x0504 body → 回傳 rarity ≥ 門檻的掉落（封包為主）。

        非 H5 / 未擷取到 / 失敗一律回 []（交由數量停滯備援處理）。
        """
        page = getattr(self.device, "_page", None)
        if page is None:
            return []
        try:
            from utils.ws_listener import drain_captured_bodies
            from utils.lamp_drop_watch import find_high_rarity_drops_in_frames
            frames = drain_captured_bodies(page)
        except Exception as exc:
            logger.warning(f"[LampService] drain 0x0504 失敗: {exc}")
            return []
        return find_high_rarity_drops_in_frames(frames, self._wanted_rarity())

    def _notify_wanted_drop(self, entry: dict) -> None:
        """開到要的品質：log + bot_state，讓腳本 / 儀表板知道（OCR 看不到的事）。"""
        try:
            from utils.lamp_drop_watch import format_drop
            desc = format_drop(entry)
        except Exception:
            desc = str(entry)
        logger.info(f"[LampService] [要的品質] 封包偵測開到: {desc}")
        if self.device_ip:
            try:
                import bot_state
                bot_state.update_state(
                    self.device_ip, task="開神燈", step=f"開到要的品質: {desc}"
                )
            except Exception as exc:
                logger.warning(f"[LampService] bot_state 更新失敗: {exc}")

    def _combo_from_packet_entry(self, entry: dict) -> Optional[str]:
        """封包為主：用封包詞條(affix_id→名稱→code)算正規化 combo，不靠 OCR。

        取前 2 個可對應到技能 code 的詞條(暈/回/連/爆/閃/技/反)；非技能詞條
        (如同伴爆擊)會被略過。回傳如 '暈回'；算不出來回 None(讓上層退回 OCR)。
        """
        try:
            from utils.web_game_api import EQUIP_AFFIX
        except Exception:
            return None
        codes: list = []
        for aid in (entry.get("affixes") or {}):
            try:
                name = EQUIP_AFFIX.get(int(aid))
            except (TypeError, ValueError):
                name = None
            if not name:
                continue
            code = self.parser.text_to_skill_code(name)
            if code and code not in codes:
                codes.append(code)
            if len(codes) == 2:
                break
        if not codes:
            return None
        return self.parser.normalize_combo("".join(codes))

    def _run_open_loop_legacy(self, start_time: float, times: float, is_compare: bool) -> None:
        """ADB(無 cocos)：沿用 LampLoopState + OCR 的舊監控迴圈。"""
        failed_reengage_streak = 0
        last_reengage_count: Optional[int] = None
        last_drop_ts: float = 0.0
        last_seen_count: Optional[int] = None
        exhaustion = LampExhaustionTracker(stop_after=self._EXHAUSTED_STOP_TICKS)

        while time.time() - start_time < times and self.state.is_running:
            # Sell-page intercept must come BEFORE tick: gold ROI on this page
            # returns the sell-all-gold total, not lamp count.
            if self.ui.is_lamp_sell_page():
                logger.debug("[LampService] 全部出售頁面，執行 click_all_sell_and_verify")
                self.ui.click_all_sell_and_verify()
                time.sleep(1)
                continue

            lamp_count = self._read_lamp_count_robust()
            has_popup = self._detect_popup()

            # 剩餘神燈歸零 → 開燈完成，停止本輪。沒有待處理彈窗時才停，避免
            # 彈窗覆蓋 count ROI 時的誤判(此時 robust read 多半回 None，本就不會觸發)。
            if not has_popup and exhaustion.is_exhausted(lamp_count):
                logger.info("[LampService] 剩餘神燈為 0，開燈完成 → 停止本輪")
                break

            if (
                lamp_count is not None
                and last_seen_count is not None
                and lamp_count < last_seen_count
            ):
                last_drop_ts = time.time()
            if lamp_count is not None:
                last_seen_count = lamp_count

            action = self.loop_state.tick(lamp_count, has_popup)

            if action == LampLoopAction.RENAVIGATE:
                logger.warning(
                    f"[LampService] count 連續 {self.loop_state.unreadable_renav_threshold} 次讀不到 → renavigate"
                )
                try:
                    self.navigate_to_lamp_adb()
                except Exception as exc:
                    logger.warning(f"[LampService] renavigate 失敗: {exc}")
                time.sleep(1.5)
                continue

            if action == LampLoopAction.WAIT:
                self._adaptive_wait(lamp_count)
                continue

            if action == LampLoopAction.REENGAGE_AUTO:
                secs_since_drop = time.time() - last_drop_ts if last_drop_ts else float("inf")
                if secs_since_drop < self._REENGAGE_PROGRESS_GRACE_SEC:
                    self._adaptive_wait(lamp_count)
                    continue
                if lamp_count is not None and lamp_count == last_reengage_count:
                    failed_reengage_streak += 1
                else:
                    failed_reengage_streak = 0
                last_reengage_count = lamp_count
                if failed_reengage_streak >= 1:
                    logger.warning(f"[LampService] reengage 後 count 仍 {lamp_count} 未動 → 強制重進")
                    try:
                        self.navigate_to_lamp_adb()
                    except Exception as exc:
                        logger.warning(f"[LampService] forced renavigate 失敗: {exc}")
                    failed_reengage_streak = 0
                    last_reengage_count = None
                    time.sleep(1.5)
                    continue
                self._reengage_auto()
                continue

            # action == HANDLE_POPUP
            if self.state.should_stop_for_incomplete_ocr(self.config.skip_incomplete_limit):
                if self._try_recover_or_stop():
                    continue
                break

            skipped = not self.process_single_lamp(is_compare)
            if skipped:
                self._record_skip_and_maybe_reengage()
            else:
                self.state.record_ocr_success()

    def navigate_to_lamp_adb(self) -> None:
        """ADB 重進燈介面 + 清殘留 + 按開始。"""
        self._enter_and_clear_adb(self._last_is_compare)
        self.ui.click_and_wait(281, 636, 2)

    def stop(self):
        """停止開神燈流程"""
        logger.info("[LampService] 收到停止指令")
        self.state.is_running = False
