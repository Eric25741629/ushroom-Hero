"""共用 scheduler gate policy。

各 scheduler 的執行流程仍留在自己的模組；這裡只收斂重複的三個 ledger
操作：啟用條件、冷卻判斷，以及完成記錄。週期/時段或 WS state 等特殊語意
可透過 hook 保留在原 scheduler，不在共用層重寫。
"""
from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


ConfigGetter = Callable[[str], Mapping[str, Any] | None]
EnabledHook = Callable[[str], bool]
DueHook = Callable[..., bool]
MarkDoneHook = Callable[..., None]


@dataclass(frozen=True)
class SchedulerPolicy:
    """描述單一 scheduler 的 gate/ledger policy。

    預設路徑覆蓋最常見的「device flag + backend + time record cooldown」
    形狀。特殊 scheduler 只需提供 hook；hook 內仍可使用原本的時間窗或
    WS state 判斷，避免把不同任務硬套成相同資料模型。
    """

    enabled_key: str | None = None
    backend: str | None = None
    record_key: str | None = None
    cooldown_seconds: float | None = None
    enabled_hook: EnabledHook | None = None
    due_hook: DueHook | None = None
    mark_done_hook: MarkDoneHook | None = None

    def is_enabled(self, ip: str, *, get_device_config: ConfigGetter) -> bool:
        if self.enabled_hook is not None:
            return bool(self.enabled_hook(ip))

        cfg = get_device_config(ip) or {}
        if self.enabled_key is not None and not bool(cfg.get(self.enabled_key, False)):
            return False
        if self.backend is not None:
            return str(cfg.get("backend", "")).lower() == self.backend
        return True

    def is_due(
        self,
        ip: str,
        now: datetime.datetime | None = None,
        *,
        return_time: Callable[..., Mapping[str, Any] | None] | None = None,
        is_record_expired: Callable[..., bool] | None = None,
        **context: Any,
    ) -> bool:
        if self.due_hook is not None:
            return bool(self.due_hook(ip, now, **context))
        if self.record_key is None:
            return True
        if return_time is None or is_record_expired is None:
            raise TypeError("record policy requires return_time and is_record_expired")
        record = return_time(ip, name=self.record_key)
        return bool(is_record_expired(record, self.cooldown_seconds))

    def mark_done(
        self,
        ip: str,
        *,
        time_recording: Callable[..., None] | None = None,
        **context: Any,
    ) -> None:
        if self.mark_done_hook is not None:
            self.mark_done_hook(ip, **context)
            return
        if self.record_key is None:
            return
        if time_recording is None:
            raise TypeError("record policy requires time_recording")
        time_recording(ip, name=self.record_key)
