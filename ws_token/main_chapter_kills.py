# -*- coding: utf-8 -*-
"""主線小怪純 WS 擊殺。

A 端登入帳號只送主線協議；B 端是免登入 ephemeral H5，只用
``chapterDataCache`` 動態查詢 A 端當前 ``part_id`` 的怪物刷新表。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Optional

from ws_token import codec
from ws_token import state as ws_state
from ws_token.abort import WSRunAborted
from ws_token.client import WSGameClient
from ws_token.creds import load_creds

logger = logging.getLogger(__name__)

CMD_INFO = 3329
CMD_ENTER = 3330
CMD_RESULT = 3331
CMD_KILL = 3332

DAILY_TARGET = 150
FRIDAY_TARGET = 3000


def target_for_day(now: Optional[datetime] = None) -> int:
    """一般日 150；星期五固定刷 3000，六、日仍只有 150。"""
    current = now or datetime.now()
    return FRIDAY_TARGET if current.weekday() == 4 else DAILY_TARGET


def build_enter(part_id: int) -> bytes:
    return codec.pb_uint(1, int(part_id))


def build_kill(part_id: int, unit_id: int, x: int, y: int) -> bytes:
    pos = codec.pb_uint(1, int(x)) + codec.pb_uint(2, int(y))
    return (
        codec.pb_uint(1, int(part_id))
        + codec.pb_uint(2, int(unit_id))
        + codec.pb_msg(3, pos)
    )


def build_result(part_id: int) -> bytes:
    return (
        codec.pb_uint(1, int(part_id))
        + codec.pb_uint(2, 1)
        + codec.pb_uint(3, 0)
    )


def _slot_pos(slot: int) -> tuple[int, int]:
    """刷新槽轉成飄字座標；伺服器戰鬥判定不依賴此座標。"""
    slot = max(1, int(slot))
    return 60 + ((slot - 1) % 3) * 150, 140 + ((slot - 1) // 3) * 90


def _abort_if_requested(should_abort: Optional[Callable[[], bool]]) -> None:
    if should_abort is not None and should_abort():
        raise WSRunAborted("main chapter kills interrupted")


def _sleep_abortable(
    seconds: float,
    should_abort: Optional[Callable[[], bool]],
) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        _abort_if_requested(should_abort)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


class ChapterRuntime:
    """免登入 B runtime；依最新 part_id 動態取得各波怪物。"""

    def __init__(
        self,
        *,
        should_abort: Optional[Callable[[], bool]] = None,
        ready_timeout_sec: float = 90.0,
    ) -> None:
        self.should_abort = should_abort
        self.ready_timeout_sec = max(1.0, float(ready_timeout_sec))
        self.pw: Any = None
        self.browser: Any = None
        self.page: Any = None
        self._cache: dict[int, list[tuple[int, int]]] = {}

    def __enter__(self) -> "ChapterRuntime":
        # Playwright 只在功能真的啟用且今日尚未完成時載入。
        from battle_calc.ephemeral_b import close_ephemeral, launch_ephemeral_b

        _abort_if_requested(self.should_abort)
        self.pw, self.browser, self.page = launch_ephemeral_b(
            headless=True, timeout_s=120
        )
        try:
            deadline = time.monotonic() + self.ready_timeout_sec
            while time.monotonic() < deadline:
                _abort_if_requested(self.should_abort)
                ready = self.page.evaluate(
                    "() => typeof chapterDataCache !== 'undefined' "
                    "&& !!chapterDataCache.getChapterConfig"
                )
                if ready:
                    return self
                _sleep_abortable(0.5, self.should_abort)
            raise RuntimeError("chapterDataCache 未在期限內 ready")
        except BaseException:
            close_ephemeral(self.pw, self.browser)
            raise

    def __exit__(self, *_exc) -> None:
        from battle_calc.ephemeral_b import close_ephemeral

        close_ephemeral(self.pw, self.browser)

    def units(self, part_id: int) -> list[tuple[int, int]]:
        part_id = int(part_id)
        if part_id in self._cache:
            return self._cache[part_id]
        rows = self.page.evaluate(
            """(partId) => {
              const c = chapterDataCache.getChapterConfig(partId);
              if (!c) return null;
              const out = [];
              for (let i = 1; i <= 5; i++) {
                const wave = c['monster_refresh' + i];
                if (!Array.isArray(wave)) continue;
                for (const row of wave) {
                  if (Array.isArray(row) && row.length >= 2) {
                    out.push([Number(row[0]), Number(row[1])]);
                  }
                }
              }
              return out;
            }""",
            part_id,
        )
        if not rows:
            raise RuntimeError(f"part {part_id} 沒有 monster_refresh 設定")
        units = [(int(unit), int(slot)) for unit, slot in rows]
        self._cache[part_id] = units
        logger.info(
            "主線擊殺 B runtime: part=%s monsters=%s",
            part_id,
            sorted({unit for unit, _slot in units}),
        )
        return units


def _load_daily_progress(
    device: str,
    *,
    date_key: str,
    target: int,
    state_dir,
) -> tuple[dict, dict, int]:
    root = ws_state.load_state(device, state_dir=state_dir)
    saved = root.get("main_chapter_kills")
    if not isinstance(saved, dict) or saved.get("date") != date_key:
        saved = {"date": date_key, "target": target, "sent": 0}
    sent = max(0, min(int(saved.get("sent", 0) or 0), target))
    saved.update({"date": date_key, "target": target, "sent": sent})
    return root, saved, sent


def run_daily(
    device: str,
    *,
    interval_sec: float = 3.0,
    persist_every: int = 10,
    should_abort: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    now: Optional[datetime] = None,
    state_dir=ws_state.STATE_DIR,
    runtime_factory=ChapterRuntime,
    client_factory=None,
) -> dict:
    """送出今日固定數量的小怪擊殺；星期五 3000，其餘日 150。

    ``sent`` 是本自動化實際收到成功回覆的數量，分批落盤，因此中斷後只補缺口。
    """
    current = now or datetime.now()
    date_key = current.date().isoformat()
    target = target_for_day(current)
    root, saved, sent = _load_daily_progress(
        device, date_key=date_key, target=target, state_dir=state_dir
    )
    if sent >= target or bool(saved.get("completed")):
        return {"skipped": "today complete", "sent": sent, "target": target}

    every = max(1, int(persist_every))
    interval = max(0.0, float(interval_sec))
    make_client = client_factory or (
        lambda: WSGameClient(load_creds(device))
    )

    def persist(*, completed: bool = False) -> None:
        saved.update({"sent": sent, "completed": bool(completed)})
        root["main_chapter_kills"] = saved
        ws_state.save_state(device, root, state_dir=state_dir)

    _abort_if_requested(should_abort)
    runtime = runtime_factory(should_abort=should_abort)
    client = make_client()
    try:
        with runtime:
            client.connect()
            info = codec.walk_dict(client.call(CMD_INFO, b""))
            part_id = int(info.get(1) or 0)
            if part_id <= 0:
                raise RuntimeError(f"main_chapter_info 缺 part_id: {info}")

            while sent < target:
                _abort_if_requested(should_abort)
                entered = codec.walk_dict(
                    client.call(CMD_ENTER, build_enter(part_id))
                )
                part_id = int(entered.get(1) or part_id)
                units = runtime.units(part_id)

                for unit_id, slot in units:
                    if sent >= target:
                        break
                    _abort_if_requested(should_abort)
                    x, y = _slot_pos(slot)
                    client.call(
                        CMD_KILL,
                        build_kill(part_id, unit_id, x, y),
                    )
                    sent += 1
                    report_now = sent % every == 0 or sent >= target
                    if report_now:
                        persist(completed=sent >= target)
                    if progress is not None and report_now:
                        progress(sent, target)
                    if sent < target:
                        _sleep_abortable(interval, should_abort)

                result = codec.walk_dict(
                    client.call(CMD_RESULT, build_result(part_id))
                )
                part_id = int(result.get(2) or part_id)
    except WSRunAborted:
        persist(completed=False)
        raise
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            logger.debug("主線擊殺 WS close 失敗: %s", device, exc_info=True)

    persist(completed=True)
    logger.info("主線擊殺完成: device=%s date=%s sent=%s", device, date_key, sent)
    return {"sent": sent, "target": target, "friday": current.weekday() == 4}
