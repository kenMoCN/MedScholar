"""Cooperative cancellation and whole-turn deadline checks."""

from __future__ import annotations

import time
from typing import Callable


class RunCancelled(RuntimeError):
    pass


class RunTimedOut(TimeoutError):
    pass


def ensure_run_active(
    cancel_check: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> None:
    if cancel_check is not None and cancel_check():
        raise RunCancelled("用户已停止本轮回答")
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise RunTimedOut("本轮回答超过总时限，已安全停止")
