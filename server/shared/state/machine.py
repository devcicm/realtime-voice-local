"""Hierarchical State Machine — base class for all FSMs.

Mirrors ``client/src/state/machine.js``.

Usage::

    class MyMachine(StateMachine):
        STATES = {
            "IDLE":   {"transitions": {"RUNNING", "ERROR"}},
            "RUNNING": {"transitions": {"IDLE", "ERROR"}},
            "ERROR":  {"transitions": {"IDLE"}},
        }
        INITIAL = "IDLE"

    m = MyMachine()
    m.transition("RUNNING", reason="start")
    assert m.current == "RUNNING"
    assert m.is_terminal() is False
    assert m.history == [("IDLE", "system-default"), ("RUNNING", "start")]
    m.transition("FOO")  # raises InvalidTransitionError
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional


class InvalidTransitionError(Exception):
    """Raised when an FSM tries to leave its state graph."""


@dataclass
class TransitionRecord:
    from_state: str
    to_state: str
    reason: str
    at_ms: float

    def to_dict(self) -> dict:
        return {
            "from": self.from_state,
            "to": self.to_state,
            "reason": self.reason,
            "at_ms": self.at_ms,
        }


class StateMachine:
    """States declared as a class attribute. ``transitions`` is the
    set of legal next states; ``initial`` is the starting state."""

    STATES: dict = {}
    INITIAL: str = ""

    def __init__(self, name: str | None = None) -> None:
        self.name = name or self.__class__.__name__
        self.current: str = self.INITIAL
        self.history: list[TransitionRecord] = [
            TransitionRecord(
                from_state="",
                to_state=self.INITIAL,
                reason="system-default",
                at_ms=_now_ms(),
            )
        ]
        self._observers: list = []

    # ---- transitions ----

    def can_transition(self, to: str) -> bool:
        allowed = self.STATES.get(self.current, {}).get("transitions", set())
        return to in allowed

    def transition(self, to: str, reason: str = "") -> "StateMachine":
        if to == self.current:
            return self
        if not self.can_transition(to):
            raise InvalidTransitionError(
                f"{self.name}: cannot transition {self.current!r} -> {to!r}. "
                f"Allowed from {self.current!r}: {sorted(self.STATES.get(self.current, {}).get('transitions', set()))}"
            )
        from_state = self.current
        self.current = to
        rec = TransitionRecord(from_state, to, reason, _now_ms())
        self.history.append(rec)
        self._notify(rec)
        return self

    def force(self, to: str, reason: str = "force") -> "StateMachine":
        """Bypass the transition table (use sparingly, for recovery)."""
        from_state = self.current
        self.current = to
        rec = TransitionRecord(from_state, to, f"[force] {reason}", _now_ms())
        self.history.append(rec)
        self._notify(rec)
        return self

    # ---- queries ----

    def is_in(self, *states: str) -> bool:
        return self.current in states

    def is_terminal(self) -> bool:
        return len(self.STATES.get(self.current, {}).get("transitions", set())) == 0

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "current": self.current,
            "history": [r.to_dict() for r in self.history],
        }

    # ---- observers ----

    def on(self, observer) -> None:
        self._observers.append(observer)

    def off(self, observer) -> None:
        if observer in self._observers:
            self._observers.remove(observer)

    def _notify(self, record: TransitionRecord) -> None:
        for obs in self._observers:
            try:
                obs(self, record)
            except Exception:
                pass


def _now_ms() -> float:
    import time
    return time.time() * 1000
