import time
import random
import os
import uiautomator2 as u2
import datetime

from miner.mining_service import run as run_mining
from miner.models.classifier import ClassifierCNN
from miner.rl.rl_recorder import RLRecorder
from utils.logging_utils import logger
from tools import click_white
from utils.ocr_clicker import click_str
from json_manager import create_store_manager
import new_cnn.cnn_model as cnn_model


def oracle(d: u2.Device, easyocr_reader=None, ip=None, clf: ClassifierCNN=None, rl_recorder: RLRecorder = None, Cnn_model=None, max_duration_minutes: float = 6.0):
    """
    挖礦主流程 (已遷移至 PaddleOCR)。
    原本的 easyocr_reader 已不再使用。
    """
    if ip is None:
        raise ValueError("ip parameter is required for oracle task.")
        
    d.click(321, 919)
    retry = 0
    while (retry < 5):
        img = d.screenshot(format='opencv')
        # 顏色檢測 - 家園
        # color check - homeland
        if Cnn_model is not None:
            print(cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')))
            if cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "homeplace":
                break
        else:
            # 如果沒有提供模型，使用點擊等待
            click_white(d)
        retry += 1
        click_white(d)
    if retry == 5:
        return
    time.sleep(1)
    d.click(101, 158)
    time.sleep(3)
    try:
        run_mining(d, ip, clf, rl_recorder=rl_recorder, max_duration_minutes=max_duration_minutes)
        # 成功後記錄進度
        from json_manager import time_recording
        time_recording(ip, name="挖礦")
        logger.info(f"[{ip}] 挖礦任務已完成並記錄。")
    except Exception as e:
        logger.error(f"連線失敗: {e}")
        d.click(500, 174)
        time.sleep(3)
        for _ in range(5):
            d.click(394+random.randint(-3,3),599+random.randint(-3,3)) #+30隻鎬子
        d.click(272, 752)
        time.sleep(3)
        click_str("確定", d, easyocr_reader)
        time.sleep(3)
        #保存截圖
        img = d.screenshot(format='opencv')
        if not os.path.exists("oracle"):
            os.makedirs("oracle")
    # cv2.imwrite("oracle/oracle_{}.jpg".format(time.time()), img)
    click_white(d)
    click_white(d)
    d.click(500, 913)
    time.sleep(3)
    d.click(321, 919)
    time.sleep(3)

def _should_perform_oracle_action(ip: str) -> bool:
    """
    判斷是否需要執行 oracle（挖礦）動作。
    修正：使用 StoreDataManager 的時間抽取工具並以管理器時區做比較，避免時區誤差造成跨日才更新的問題。
    Returns True 表示應該執行。
    """
    store_manager = create_store_manager(ip)
    record = store_manager.get_purchase_record("挖礦")
    if not record:
        logger.info("未找到'挖礦'記錄，將執行購買流程。")
        return True

    # 嘗試以管理器的工具解析為 local aware datetime（已含時區）
    try:
        local_dt = store_manager._extract_last_time_as_local(record)
    except Exception:
        local_dt = None

    # 如果解析失敗，嘗試用 last_time_utc / last_time 回退處理
    if local_dt is None:
        # 優先使用標準 UTC 欄位
        try:
            utc_str = record.get("last_time_utc") or record.get("last_time")
            if utc_str:
                # 若是 last_time_utc (ISO)，用 parse 工具；若是 legacy last_time，視為 UTC 再轉回 local
                dt_utc = None
                from json_manager import StoreDataManager  # type: ignore
                # 嘗試解析 ISO 或舊格式
                try:
                    dt_utc = store_manager._parse_iso_as_utc(utc_str)
                except Exception:
                    dt_utc = None
                if dt_utc is None:
                    try:
                        dt_utc = store_manager._parse_legacy_naive_as_utc(utc_str)
                    except Exception:
                        dt_utc = None
                if dt_utc:
                    try:
                        local_dt = dt_utc.astimezone(store_manager.timezone)
                    except Exception:
                        local_dt = None
        except Exception:
            local_dt = None

    if local_dt is None:
        logger.warning("記錄時間無法解析，將執行購買流程。")
        return True

    now_local = datetime.datetime.now(store_manager.timezone)
    # 閾值：1 小時
    if (now_local - local_dt) > datetime.timedelta(hours=1):
        logger.info(f"上次'挖礦'時間為 {local_dt}, 已超過1小時，將執行購買流程。")
        return True
    else:
        logger.info(f"上次'挖礦'時間為 {local_dt}, 尚未超過1小時，不執行。")
        return False

