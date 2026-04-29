"""
神燈服務 - 主流程協調器

整合所有組件，提供完整的開神燈流程
"""

import time
from typing import Optional, List, Tuple, Callable
import numpy as np

from .config import OpenGoldConfig
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
        device_ip: Optional[str] = None
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

    def _read_pairs_from_rois(self, rois: List[np.ndarray]) -> List[Tuple[str, Optional[float]]]:
        """從多個 ROI 讀取 (skill_code, prob) 列表"""
        pairs = []
        for roi in rois:
            try:
                res = self.analyze_skill_fn(roi)
                txt = self.parser.get_first_text_from_skill_result(res)
                skill_code, prob = self.parser.parse_skill_prob(txt)
                pairs.append((skill_code, prob))
                print(f"[LampService] OCR 結果: '{txt}' -> ({skill_code}, {prob})")
            except Exception as e:
                print(f"[LampService] ROI 讀取失敗: {e}")
                pairs.append((None, None))
        return pairs

    def _is_pairs_incomplete(self, pairs: List[Tuple]) -> bool:
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

    def _is_prob_too_large(self, pairs: List[Tuple], threshold: float = 10.0) -> bool:
        try:
            for p in (pairs or []):
                if not p or len(p) < 2:
                    continue
                prob = p[1]
                if prob is None:
                    continue
                try:
                    if float(prob) > float(threshold):
                        return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    def _detect_lian_shan_equip(self) -> bool:
        try:
            stage_img = self.ui.get_stage_roi()
            stage_result = self.analyze_stage_fn(stage_img)
            return self.device_detector.detect_lian_shan_from_ocr_result(stage_result)
        except Exception as e:
            print(f"[LampService] 偵測連閃裝備失敗: {e}，預設為 True")
            return True

    def _log_screenshot(self, prefix: str = "lamp", suffix: str = ""):
        try:
            self.screenshot_logger.save_screenshot_from_device(
                self.device, prefix=prefix, suffix=suffix
            )
        except Exception as e:
            print(f"[LampService] 截圖記錄失敗: {e}")

    def _log_roi(self, roi: np.ndarray, label: str):
        """儲存 ROI 小圖供後續調試"""
        try:
            self.screenshot_logger.save_roi(roi, prefix="roi", suffix=label)
        except Exception as e:
            print(f"[LampService] ROI 截圖記錄失敗: {e}")

    def process_single_lamp(self, is_compare: bool = True) -> bool:
        """
        處理單次開神燈

        回傳:
        - True: 正常完成
        - False: 因 OCR 不完整而跳過
        """
        self._log_screenshot(prefix="lamp", suffix="start")

        if self.ui.is_lamp_sell_page():
            print("[LampService] 當前在全部出售頁面")
            self.ui.click_all_sell()
            return True

        gold_num = self.ui.get_gold_num()
        if gold_num is not None:
            print(f"[LampService] 神燈數量: {gold_num}")
            if self.state.last_gold_num is not None and gold_num != self.state.last_gold_num:
                print(f"[LampService] 神燈數量變化: {self.state.last_gold_num} -> {gold_num}")
                time.sleep(10)
                self.state.last_gold_num = gold_num
                return True
            self.state.last_gold_num = gold_num

        self.ui.click_lamp_button()
        time.sleep(1)

        # 階段1：開燈後畫面（看到技能結果）
        self._log_screenshot(prefix="lamp", suffix="opened")

        # 開燈後再次確認：若出現全部出售頁面（鎏金批量結算），直接賣掉並返回
        if self.ui.is_lamp_sell_page():
            print("[LampService] 開燈後出現全部出售頁面，執行全部出售")
            self.ui.click_all_sell()
            return True

        skill_roi = self.ui.get_skill_roi()
        # 儲存技能 ROI（供 OCR 座標校準用）
        self._log_roi(skill_roi, "skill_roi")

        skill_result = self.analyze_skill_fn(skill_roi)

        if not skill_result or not skill_result.get('success'):
            print("[LampService] 技能 OCR 失敗")
            self._log_screenshot(prefix="lamp", suffix="ocr_fail")
            return True

        ocr_results_raw = skill_result.get('ocr_results', [])
        parsed = self.parser.parse_ocr(ocr_results_raw)

        combo = parsed.combo_raw
        normalized_combo = parsed.combo_norm
        is_unwanted = parsed.unwanted

        print(f"[LampService] 技能組合: {combo} => {normalized_combo} ; 不要? {is_unwanted}")

        if is_unwanted:
            print("[LampService] 不需要的組合，執行出售")
            # 階段2：不要的組合出售
            self._log_screenshot(prefix="lamp", suffix="sold_unwanted")
            self.ui.click_sell_button()
            self.state.record_ocr_success()
        else:
            print("[LampService] 需要的組合，進入比較流程")
            skipped = self._process_wanted_combo(normalized_combo, is_compare)
            if skipped:
                return False

        return True

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
            print("[LampService] 無法識別階段資訊")
            return False

        stage_texts = stage_result.get('stage_texts', [])
        stage_texts = [self.parser.normalize_stage_name(t) for t in stage_texts]
        stage_texts = [t for t in stage_texts if len(t) >= 2]

        print(f"[LampService] 正規化後階段: {stage_texts}")

        if self.has_lian_shan_equip is None:
            self.has_lian_shan_equip = self._detect_lian_shan_equip()
            print(f"[LampService] 連閃裝備偵測結果: {self.has_lian_shan_equip}")

        if combo in stage_texts:
            index = stage_texts.index(combo)
            print(f"[LampService] 找到階段 '{combo}' 在索引: {index}")
            self.ui.select_stage(index)
            return self._execute_upgrade_sequence(index, stage_texts, is_compare)
        else:
            print(f"[LampService] 未找到階段 '{combo}'，可用階段: {stage_texts}")
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

        print(f"[LampService] rolled pairs: {rolled_pairs}")
        print(f"[LampService] original pairs: {original_pairs}")

        if self._is_pairs_incomplete(original_pairs):
            print("[LampService] 原有詞條解析不完整，嘗試使用備用 ROI 重試...")
            alt_pairs = self._read_pairs_from_rois(orig_rois_secondary)
            if not self._is_pairs_incomplete(alt_pairs):
                original_pairs = alt_pairs
                print("[LampService] 採用備用 ROI 的解析結果")

        if self._is_prob_too_large(original_pairs):
            print("[LampService] 原有詞條出現超過 10% 的機率，視為 OCR 誤判 -> 嘗試備用 ROI...")
            alt_pairs = self._read_pairs_from_rois(orig_rois_secondary)
            if not self._is_pairs_incomplete(alt_pairs) and not self._is_prob_too_large(alt_pairs):
                original_pairs = alt_pairs
                print("[LampService] 採用備用 ROI 的解析結果（通過機率合理性檢查）")

        if self._is_pairs_incomplete(rolled_pairs) or self._is_pairs_incomplete(original_pairs):
            print("[LampService] 警告：OCR 無法完整辨識詞條，跳過自動比較")
            # 階段5：OCR 不完整（診斷截圖）
            self._log_screenshot(prefix="lamp", suffix="incomplete_ocr")
            return True

        rolled_equip = Equipment.from_pairs(rolled_pairs)
        original_equip = Equipment.from_pairs(original_pairs)

        evaluator = SkillEvaluator(self.config, self.has_lian_shan_equip)
        result = evaluator.compare_skill_pairs(rolled_equip, original_equip, is_compare)

        print(f"[LampService] 比對結果: replace={result.should_replace} ; reason: {result.reason}")

        if result.should_replace:
            print("[LampService] 執行換裝")
            # 階段6a：決定保留（換裝）
            self._log_screenshot(prefix="lamp", suffix="kept")
            self.ui.click_keep_button()
        else:
            print("[LampService] 不換，執行出售")
            # 階段6b：決定出售（不換）
            self._log_screenshot(prefix="lamp", suffix="sold")
            self.ui.click_sell_button()

        self._return_to_original_equipment(stage_texts)

        return False

    def _return_to_original_equipment(self, original_stage_texts: List[str]):
        """切換回原始裝備"""
        self.ui.click_and_wait(419, 720, 3)
        self.ui.click_and_wait(272, 796, 1)
        self.ui.click_and_wait(281, 350, 1)

        stage_img = self.ui.get_stage_roi_recheck()
        stage_result = self.analyze_stage_fn(stage_img)

        if stage_result and stage_result.get('success'):
            current_stage_texts = stage_result.get("stage_texts", [])
            current_stage_texts = [t for t in current_stage_texts if len(t) >= 2]

            original_combo = original_stage_texts[0]
            if original_combo in current_stage_texts:
                original_index = current_stage_texts.index(original_combo)
                print(f"[LampService] 切換回原始裝備 '{original_combo}' 在索引: {original_index}")
                self.ui.select_stage(original_index)
            else:
                print(f"[LampService] 警告：未找到原始裝備 '{original_combo}'，點擊第一個")
                self.ui.select_stage(0)
        else:
            print("[LampService] 警告：無法重新識別階段，點擊第一個")
            self.ui.select_stage(0)

        self.ui.close_upgrade_panel()

    def run(self, times: int = 1000, is_compare: bool = True):
        """
        執行開神燈主流程

        參數:
        - times: 持續秒數，-1 表示無限
        - is_compare: 是否進行機率比對
        """
        start_time = time.time()
        if times == -1:
            times = float('inf')

        print(f"[LampService] 開始開神燈，設定: times={times}, is_compare={is_compare}")

        self.ui.navigate_to_lamp()
        self.state.is_running = True

        auto_retry_limit = 2  # 連續失敗幾次後嘗試重新啟用自動模式
        try:
            while time.time() - start_time < times and self.state.is_running:
                if self.state.should_stop_for_incomplete_ocr(self.config.skip_incomplete_limit):
                    # 在中止前先確認神燈是否真的開完了：若剩餘 > 0，
                    # 視為 OCR/UI 暫時失常而非任務完成，重置計數並嘗試重新啟用自動模式繼續。
                    remaining = self.ui.get_remaining_lamp_count()
                    if remaining is not None and remaining > 0:
                        print(
                            f"[LampService] 連續跳過 {self.config.skip_incomplete_limit} 次 OCR 不完整，"
                            f"但神燈剩餘 {remaining} 顆，重置計數並嘗試恢復後繼續。"
                        )
                        self.state.record_ocr_success()
                        try:
                            self.ui.click_auto_mode_button()
                            self.ui.click_start_confirm()
                        except Exception as recover_exc:
                            print(f"[LampService] 嘗試恢復自動模式失敗: {recover_exc}")
                        continue
                    print(
                        f"[LampService] 連續跳過 {self.config.skip_incomplete_limit} 次 OCR 不完整，"
                        f"剩餘={remaining}，停止。"
                    )
                    break

                skipped = not self.process_single_lamp(is_compare)

                if skipped:
                    count = self.state.record_ocr_incomplete()
                    print(f"[LampService] OCR 不完整已跳過次數: {count}/{self.config.skip_incomplete_limit}")
                    # 連續失敗可能是自動模式未啟動，嘗試重新點擊自動+開始
                    if count % auto_retry_limit == 0:
                        print("[LampService] 嘗試重新啟用自動模式...")
                        self.ui.click_auto_mode_button()
                        self.ui.click_start_confirm()
                else:
                    self.state.record_ocr_success()

        finally:
            print("[LampService] 結束開神燈流程")
            self.ui.exit_lamp()
            self.state.is_running = False

    def stop(self):
        """停止開神燈流程"""
        print("[LampService] 收到停止指令")
        self.state.is_running = False
