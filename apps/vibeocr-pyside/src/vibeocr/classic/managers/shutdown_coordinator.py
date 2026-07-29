"""应用级关闭协调器：单一 wall-clock 预算、有序且可观测地 drain。"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShutdownStepResult:
    name: str
    status: str
    elapsed_ms: int
    allowance_ms: int


@dataclass(frozen=True)
class _ShutdownStep:
    name: str
    fn: Callable[[], object]
    max_timeout_ms: int | None
    continue_on_timeout: bool


class ShutdownCoordinator:
    """按注册顺序从同一个绝对截止时间扣减关闭预算。

    每一步可声明自己的最长等待，但不会再把总预算机械均分。前一步快速完成时，
    剩余时间会完整留给后续步骤；超时步骤是否允许后续继续由调用方显式决定。
    """

    def __init__(self) -> None:
        self._steps: list[_ShutdownStep] = []
        self.results: list[ShutdownStepResult] = []

    def register(
        self,
        name: str,
        shutdown_fn: Callable[[], object],
        *,
        max_timeout_ms: int | None = None,
        continue_on_timeout: bool = True,
    ) -> None:
        if max_timeout_ms is not None and max_timeout_ms < 0:
            raise ValueError("max_timeout_ms must be non-negative")
        self._steps.append(
            _ShutdownStep(
                name=name,
                fn=shutdown_fn,
                max_timeout_ms=max_timeout_ms,
                continue_on_timeout=continue_on_timeout,
            )
        )

    def coordinate(self, timeout_ms: int = 5000) -> bool:
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        if not self._steps:
            return True

        self.results.clear()
        deadline = time.monotonic() + timeout_ms / 1000
        all_ok = True

        for step in self._steps:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            allowance_ms = remaining_ms
            if step.max_timeout_ms is not None:
                allowance_ms = min(allowance_ms, step.max_timeout_ms)
            if allowance_ms <= 0:
                self.results.append(
                    ShutdownStepResult(step.name, "budget_exhausted", 0, 0)
                )
                logger.warning(
                    "shutdown step skipped: budget exhausted",
                    extra={"event": "shutdown.step", "step": step.name},
                )
                all_ok = False
                break

            done = threading.Event()
            exception: list[BaseException] = []
            outcome: list[object] = []

            def run(
                fn=step.fn, errors=exception, result=outcome, signal=done
            ) -> None:
                try:
                    result.append(fn())
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    signal.set()

            started = time.monotonic()
            threading.Thread(
                target=run,
                name=f"shutdown-{step.name}",
                daemon=True,
            ).start()
            finished = done.wait(allowance_ms / 1000)
            elapsed_ms = int((time.monotonic() - started) * 1000)

            if not finished:
                self.results.append(
                    ShutdownStepResult(
                        step.name, "timeout", elapsed_ms, allowance_ms
                    )
                )
                logger.warning(
                    "shutdown step timed out",
                    extra={
                        "event": "shutdown.step",
                        "step": step.name,
                        "elapsed_ms": elapsed_ms,
                        "allowance_ms": allowance_ms,
                    },
                )
                all_ok = False
                if not step.continue_on_timeout:
                    break
                continue

            if exception:
                self.results.append(
                    ShutdownStepResult(
                        step.name, "error", elapsed_ms, allowance_ms
                    )
                )
                logger.error(
                    "shutdown step failed",
                    exc_info=exception[0],
                    extra={
                        "event": "shutdown.step",
                        "step": step.name,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                all_ok = False
                if not step.continue_on_timeout:
                    break
                continue

            if outcome and outcome[0] is False:
                self.results.append(
                    ShutdownStepResult(
                        step.name, "failed", elapsed_ms, allowance_ms
                    )
                )
                logger.warning(
                    "shutdown step reported incomplete drain",
                    extra={
                        "event": "shutdown.step",
                        "step": step.name,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                all_ok = False
                if not step.continue_on_timeout:
                    break
                continue

            self.results.append(
                ShutdownStepResult(step.name, "completed", elapsed_ms, allowance_ms)
            )
            logger.debug(
                "shutdown step completed",
                extra={
                    "event": "shutdown.step",
                    "step": step.name,
                    "elapsed_ms": elapsed_ms,
                },
            )

        return all_ok


__all__ = ["ShutdownCoordinator", "ShutdownStepResult"]
