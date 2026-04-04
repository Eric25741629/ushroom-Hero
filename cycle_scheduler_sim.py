import datetime
from typing import Optional, Dict, Tuple, List

"""週期計算與模擬工具

這個模組有兩個用途：

1. 給 `new_main_before20250514.py` 等主程式匯入，用來判斷
   「某個行為是否在這一週應該執行」──介面接受 `return_time` 回傳的 record。
2. 直接執行本檔 (`python cycle_scheduler_sim.py`) 來做純模擬，
   看每一週會不會被判定為執行週。
"""

TPE = datetime.timezone(datetime.timedelta(hours=8))


def _monday_of(date_obj: datetime.date) -> datetime.date:
    """取得某日所在週的週一日期（ISO: 週一是 0）。"""
    return date_obj - datetime.timedelta(days=date_obj.weekday())


def should_execute_cycle_from_record(
    record: Optional[Dict],
    *,
    cycle_weeks: int = 4,
    today: Optional[datetime.date] = None,
    allowed_weekdays: Optional[List[int]] = None,
) -> Tuple[bool, bool]:
    """給 new_main 呼叫的介面：從 `return_time` 的 record 判斷是否該執行。

    record 來源預期為 `json_manager.return_time(ip, name=...)`，格式例如：
        {
            "timestamp": 1730000000.0,
            "recorded_date": "2025-10-28",
            "is_next_day": false,
            "is_next_week": false,
        }

    Args:
        record: `return_time` 回傳的 dict，或 None（表示從未執行）。
        cycle_weeks: 每幾週執行一次（例：4 代表 4 週執行 1 週）。
        today: 要判斷的日期，不給就用現在台灣時間日期。
        allowed_weekdays: 允許執行的星期幾列表（0=一, 6=日）。
            - None: 這一週的任何一天都會回傳 True（只看週期）。
            - 例如 [1, 2]: 只在每個啟用週的星期二/三回傳 True。

    Returns:
        (should_execute, need_week_record)
        - should_execute: 今天是否應該執行
        - need_week_record: 是否代表這是第一次（呼叫端可用來記錄週期起始時間）
    """
    if today is None:
        today = datetime.datetime.now(TPE).date()

    # 完全沒有紀錄：第一次一定執行，並且需要建立週期起始紀錄
    if record is None:
        return True, True

    recorded_date: Optional[datetime.date] = None

    # 先嘗試用 recorded_date 欄位
    if isinstance(record, dict) and record.get("recorded_date"):
        try:
            recorded_date = datetime.datetime.strptime(record["recorded_date"], "%Y-%m-%d").date()
        except Exception:
            recorded_date = None

    # 再退而求其次，用 timestamp 轉日期
    if recorded_date is None and isinstance(record, dict) and record.get("timestamp"):
        try:
            ts = float(record["timestamp"])
            recorded_date = datetime.datetime.fromtimestamp(ts, TPE).date()
        except Exception:
            recorded_date = None

    # 如果還是拿不到合法日期，就當成沒有紀錄
    if recorded_date is None:
        return True, True

    current_monday = _monday_of(today)
    recorded_monday = _monday_of(recorded_date)

    weeks_since = (current_monday - recorded_monday).days // 7
    if weeks_since < 0:
        weeks_since = 0

    # 先判斷這一週是否為啟用週
    in_active_week = (weeks_since % cycle_weeks) == 0
    if not in_active_week:
        return False, False

    # 若未指定星期限制，啟用週內每天都 True
    if not allowed_weekdays:
        should_execute = True
    else:
        # 只允許在指定 weekday 執行
        should_execute = today.weekday() in allowed_weekdays

    # 這裡沿用原本邏輯：只有「第一次完全沒紀錄」時 need_week_record 才為 True。
    need_week_record = False

    return should_execute, need_week_record


# ===== 以下為模擬工具，方便離線測試週期結果 =====

def simulate_weeks(
    first_run_date: str,
    *,
    cycle_weeks: int = 4,
    total_weeks: int = 16,
    allowed_weekdays: Optional[List[int]] = None,
) -> List[Tuple[datetime.date, bool]]:
    """模擬：假設第一次在 first_run_date 執行，之後 N 週的執行情況。

    這裡會用 first_run_date 當成 recorded_date，
    每週一丟進 `should_execute_cycle_from_record` 看是否為啟用週。
    """
    first_dt = datetime.datetime.strptime(first_run_date, "%Y-%m-%d").date()
    first_monday = _monday_of(first_dt)

    # 在 Windows / 某些 Python 實作中不支援 "%s"，改用 timestamp()
    first_dt_ts = datetime.datetime(first_dt.year, first_dt.month, first_dt.day, tzinfo=TPE).timestamp()
    fake_record = {"recorded_date": first_dt.strftime("%Y-%m-%d"), "timestamp": first_dt_ts}

    rows: List[Tuple[datetime.date, bool]] = []
    for i in range(total_weeks):
        day = first_monday + datetime.timedelta(weeks=i)
        should, _ = should_execute_cycle_from_record(
            fake_record,
            cycle_weeks=cycle_weeks,
            today=day,
            allowed_weekdays=allowed_weekdays,
        )
        rows.append((day, should))
    return rows


def pretty_print_simulation(first_run_date: str, cycle_weeks: int = 4, total_weeks: int = 16,
                            allowed_weekdays: Optional[List[int]] = None) -> None:
    """在終端機印出模擬結果，方便肉眼確認週期與實際執行日。

    - 若未指定 allowed_weekdays：顯示哪些週是啟用週。
    - 若有指定 allowed_weekdays：顯示每個啟用週中實際會執行的日期。
    """
    print(f"模擬設定：第一次執行日 = {first_run_date}，每 {cycle_weeks} 週執行一次，總共模擬 {total_weeks} 週\n")

    # 先準備一個 record，專門用來判斷「這一週是不是啟用週」
    first_dt = datetime.datetime.strptime(first_run_date, "%Y-%m-%d").date()
    record_for_active = {"recorded_date": first_dt.strftime("%Y-%m-%d")}

    rows = simulate_weeks(first_run_date, cycle_weeks=cycle_weeks, total_weeks=total_weeks,
                          allowed_weekdays=allowed_weekdays)

    week_names = "一二三四五六日"

    for monday, _ in rows:
        # 先看這一週是不是啟用週（不考慮星期幾限制）
        active_week, _ = should_execute_cycle_from_record(
            record_for_active,
            cycle_weeks=cycle_weeks,
            today=monday,
            allowed_weekdays=None,
        )

        if not active_week:
            print(f"週一 {monday.isoformat()}  ->  休息（非啟用週）")
            continue

        # 啟用週：若沒指定 weekday 限制，代表一週內每天都可執行
        if not allowed_weekdays:
            print(f"週一 {monday.isoformat()}  ->  ✔ 啟用週（本週任意日皆可執行）")
            continue

        # 有指定 weekday：列出本週實際會執行的日期
        run_days: List[str] = []
        for offset in range(7):
            day = monday + datetime.timedelta(days=offset)
            if day.weekday() in allowed_weekdays:
                run_days.append(f"{day.isoformat()}(週{week_names[day.weekday()]})")

        if run_days:
            print(f"週一 {monday.isoformat()}  ->  ✔ 啟用週，執行日：{', '.join(run_days)}")
        else:
            print(f"週一 {monday.isoformat()}  ->  ✔ 啟用週，但本設定沒有符合的執行日？")


if __name__ == "__main__":
    # ===== 使用方式說明 =====
    # 1. 修改下面這幾個變數
    # 2. 在專案根目錄執行：
    #    python cycle_scheduler_sim.py
    # 3. 看輸出結果，就知道哪幾週會執行

    # 第一次實際執行任務的日期（你可以改成真實的）
    FIRST_RUN_DATE = "2026-01-20"  # 範例：第一次執行在 2026-01-20

    # 每幾週執行一次，例如 4 代表「每 4 週執行 1 週」
    CYCLE_WEEKS = 4

    # 要往後模擬幾週
    TOTAL_WEEKS = 16

    pretty_print_simulation(FIRST_RUN_DATE, cycle_weeks=CYCLE_WEEKS, total_weeks=TOTAL_WEEKS, allowed_weekdays=[1]  )  # 只在每個啟用週的星期二執行