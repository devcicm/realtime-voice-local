"""Turn — one entry in the conversation log.

States:
  streaming    - the LLM is currently generating tokens for this turn
  finalized    - the turn was committed (LLM finished + audio delivered OR
                 user input was complete)
  canceled     - the turn was canceled before/during delivery (barge-in,
                 system error, user abort). NOT injected to the LLM
                 unless cancel mode = 'note'/'summary' injects a
                 synthetic system turn instead.

The turn has a stable id (uuid) for cross-system correlation. Multiple
turns can coexist in `streaming` state across the system (a user turn
being assembled in parallel with an assistant turn being delivered),
but only ONE finalized assistant turn exists at a time.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Literal, Optional


def _new_id() -> str:
    return secrets.token_hex(8)


VALID_STATUSES = ("streaming", "finalized", "interrupted", "canceled")


@dataclass
class Turn:
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    status: Literal["streaming", "finalized", "interrupted", "canceled"] = "finalized"
    source: str = "text"                    # for user turns: 'text' | 'stt'
    reasoning: Optional[str] = None          # for assistant turns
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    finalized_at_ms: Optional[float] = None
    canceled_at_ms: Optional[float] = None
    interrupted_at_ms: Optional[float] = None
    cancel_reason: Optional[str] = None
    interrupted_reason: Optional[str] = None
    partial_at_cancel: Optional[str] = None  # snapshot of content when canceled
    partial_at_interrupt: Optional[str] = None  # snapshot of content when interrupted
    truncated_at_ms: Optional[float] = None  # when truncate() was applied

    def __post_init__(self) -> None:
        # Validate status at runtime (dataclass doesn't enforce Literal).
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid Turn status: {self.status!r}. "
                f"Must be one of {VALID_STATUSES}"
            )
        # If the turn is created already finalized (default), stamp
        # the finalized timestamp. Streaming turns set it later.
        if self.status == "finalized" and self.finalized_at_ms is None:
            self.finalized_at_ms = time.time() * 1000

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "status": self.status,
            "source": self.source,
            "reasoning": self.reasoning,
            "created_at_ms": self.created_at_ms,
            "finalized_at_ms": self.finalized_at_ms,
            "canceled_at_ms": self.canceled_at_ms,
            "interrupted_at_ms": self.interrupted_at_ms,
            "cancel_reason": self.cancel_reason,
            "interrupted_reason": self.interrupted_reason,
            "partial_at_cancel": self.partial_at_cancel,
            "partial_at_interrupt": self.partial_at_interrupt,
            "truncated_at_ms": self.truncated_at_ms,
        }
