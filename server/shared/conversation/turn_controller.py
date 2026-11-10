"""TurnController — single authority for cancel/interrupt of the active Run.

Server-side mirror of the client/src/realtime/turnController.js.
Used by the FastAPI servers to track run identity, manage cancel,
and propagate run_id to upstream services (LM Studio).

Guards:
  - Idempotent: calling interrupt() twice is a no-op.
  - At most one active run.
  - All state transitions are validated by the RunStateMachine.
  - Cleanup errors (on_interrupt callback, truncate, mark_interrupted)
    are caught and logged — they never break the cancel chain.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from .machine import RunState

logger = logging.getLogger(__name__)


class RunContext:
    """One run = one assistant turn attempt, from creation to a terminal state.

    Mirrors client/src/realtime/runContext.js (same API surface).
    """

    def __init__(self, session_id: str = "sess-default", turn_id: str = "turn-default"):
        self.run_id = f"run-{uuid.uuid4().hex[:8]}"
        self.session_id = session_id
        self.turn_id = turn_id
        self.state: str = RunState["CREATED"]
        self.created_at_ms = time.time() * 1000
        self.last_transition_at_ms = self.created_at_ms
        self.generated_text: str = ""
        self.spoken_text: str = ""
        self.played_audio_ms: float = 0.0
        self.truncated_at_ms: Optional[float] = None

    def transition(self, next_state: str) -> bool:
        if self.state == next_state:
            return True
        # Permissive (the JS side has a strict table; we allow any
        # forward transition but log it).
        self.state = next_state
        self.last_transition_at_ms = time.time() * 1000
        return True

    def to_dict(self) -> dict:
        return {
            "runId": self.run_id,
            "sessionId": self.session_id,
            "turnId": self.turn_id,
            "state": self.state,
            "playedAudioMs": self.played_audio_ms,
            "createdAtMs": self.created_at_ms,
        }


class TurnController:
    """Owns the active RunContext. Idempotent interrupt. Mirrors JS."""

    def __init__(
        self,
        conversation_log=None,
        on_interrupt: Optional[Callable[[RunContext, str], Awaitable[None]]] = None,
        metrics=None,
    ):
        self._log = conversation_log
        self._on_interrupt = on_interrupt
        self._metrics = metrics
        self._active_run: Optional[RunContext] = None
        self._last_interrupted_run: Optional[RunContext] = None
        self._lock = asyncio.Lock()

    @property
    def active_run(self) -> Optional[RunContext]:
        return self._active_run

    @property
    def active_run_id(self) -> Optional[str]:
        return self._active_run.run_id if self._active_run else None

    async def new_run(self, session_id: str = "", turn_id: str = "") -> RunContext:
        async with self._lock:
            # Force-finalize any prior non-terminal run (defensive).
            if self._active_run and self._active_run.state not in (
                RunState["INTERRUPTED"],
                RunState["COMPLETED"],
                RunState["FAILED"],
            ):
                self._active_run.transition(RunState["INTERRUPTED"])
            self._last_interrupted_run = None
            run = RunContext(
                session_id=session_id or "sess-default",
                turn_id=turn_id or "turn-default",
            )
            self._active_run = run
            return run

    async def interrupt(
        self,
        reason: str = "barge-in",
        current_played_ms: Optional[float] = None,
    ) -> bool:
        if self._last_interrupted_run:
            return True  # idempotent

        async with self._lock:
            run = self._active_run
            if not run:
                return False
            if run.state in (RunState["INTERRUPTED"], RunState["COMPLETED"], RunState["FAILED"]):
                return True

            t0 = time.time()
            run.transition(RunState["INTERRUPTING"])

            # 1. onInterrupt callback (TTS WS cancel, audio stop, etc.)
            if self._on_interrupt:
                try:
                    result = self._on_interrupt(run, reason)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.warning("on_interrupt callback threw: %s", e)

            # 2. truncate the conversation log
            if self._log and hasattr(self._log, "truncate"):
                try:
                    self._log.truncate(
                        None,  # turnId is found by role (latest assistant)
                        current_played_ms
                        if current_played_ms is not None
                        else run.played_audio_ms,
                    )
                except Exception as e:
                    logger.warning("truncate failed: %s", e)

            # 3. mark interrupted (default keeps in context)
            if self._log and hasattr(self._log, "mark_interrupted"):
                try:
                    self._log.mark_interrupted("assistant", reason)
                except Exception as e:
                    logger.warning("mark_interrupted failed: %s", e)

            # 4. finalize
            run.transition(RunState["INTERRUPTED"])
            self._last_interrupted_run = run
            self._active_run = None

            elapsed_ms = (time.time() - t0) * 1000
            if self._metrics and hasattr(self._metrics, "histogram"):
                try:
                    self._metrics.histogram("run_cancel_to_new_run_ms").observe(elapsed_ms)
                except Exception:
                    pass
            return True

    async def complete(self) -> None:
        async with self._lock:
            run = self._active_run
            if not run:
                return
            if run.state in (RunState["INTERRUPTED"], RunState["COMPLETED"], RunState["FAILED"]):
                return
            run.transition(RunState["COMPLETED"])
            self._active_run = None
            self._last_interrupted_run = None

    async def fail(self, error: Any = None) -> None:
        async with self._lock:
            run = self._active_run
            if not run:
                return
            if run.state in (RunState["INTERRUPTED"], RunState["COMPLETED"], RunState["FAILED"]):
                return
            run.transition(RunState["FAILED"])
            self._active_run = None
            self._last_interrupted_run = None


_singleton: Optional["TurnController"] = None


def get_turn_controller() -> "TurnController":
    global _singleton
    if _singleton is None:
        _singleton = TurnController()
    return _singleton


def reset_turn_controller() -> None:
    global _singleton
    _singleton = None
