"""Fault injection for recovery tests and the slice-2 demo (prompt §30). LOCAL/TEST ONLY.

A spec is `point:target:action[,...]`, e.g. `after_effect:notify_assignee:crash`. `crash` raises
SimulatedCrash - a BaseException, so it escapes every `except Exception` exactly like a killed
process would; `sleep=<s>` stalls. The worker refuses fault specs outside local/test.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class SimulatedCrash(BaseException):
    """Abrupt stop: nothing after the injection point runs (no cleanup, no commit)."""


@dataclass
class FaultInjector:
    rules: dict[tuple[str, str], str] = field(default_factory=dict)
    fired: list[tuple[str, str]] = field(default_factory=list)
    once: bool = True

    @classmethod
    def parse(cls, spec: str | None) -> FaultInjector:
        rules: dict[tuple[str, str], str] = {}
        for part in filter(None, (spec or "").split(",")):
            point, target, action = part.strip().split(":", 2)
            rules[(point, target)] = action
        return cls(rules)

    def hit(self, point: str, target: str) -> None:
        action = self.rules.get((point, target)) or self.rules.get((point, "*"))
        if action is None:
            return
        if self.once:
            self.rules.pop((point, target), None)
            self.rules.pop((point, "*"), None)
        self.fired.append((point, target))
        if action == "crash":
            raise SimulatedCrash(f"{point}:{target}")
        if action.startswith("sleep="):
            time.sleep(float(action.split("=", 1)[1]))
        if action == "error":
            raise RuntimeError(f"injected transient error at {point}:{target}")


NO_FAULTS = FaultInjector()
