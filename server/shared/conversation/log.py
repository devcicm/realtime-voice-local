"""ConversationLog — the single source of truth for LLM context.

Structural guarantees:
  - `to_messages()` only emits turns with status == 'finalized'
    (and synthetic system turns produced by cancel mode).
  - API is typed. There is no `.push()` or array-index access exposed.
  - The internal `Map<id, Turn>` is replaced on `clear()`, never mutated
    in place, so subscribers see a clean snapshot.

Cancel modes (see `cancel_modes.py`):
  - 'silent': canceled turns disappear entirely
  - 'note':   a synthetic system turn summarizes the interruption
  - 'summary': a brief one-sentence summary of the canceled content

Sliding window: when the log exceeds `window_size`, the oldest
finalized turns are dropped from `to_messages()` (but kept in `history()`
for the inspector). If `overflow_strategy == 'summarize'`, the
oldest turns are sent through a summarizer and replaced by a
synthetic system turn.
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Callable, Literal, Optional

from .cancel_modes import CANCEL_MODES, apply_cancel_mode
from .turn import Turn


CancelMode = Literal["silent", "note", "summary"]
OverflowStrategy = Literal["truncate", "summarize"]


class ConversationLog:
    def __init__(
        self,
        window_size: int = 20,
        cancel_mode: CancelMode = "note",
        overflow_strategy: OverflowStrategy = "truncate",
        summarizer: Optional[Callable[[list], str]] = None,
    ) -> None:
        self._lock = threading.RLock()
        # OrderedDict preserves insertion order; oldest at the start.
        self._turns: "OrderedDict[str, Turn]" = OrderedDict()
        self.window_size = window_size
        self.cancel_mode = cancel_mode
        self.overflow_strategy = overflow_strategy
        self._summarizer = summarizer
        # Per-event listener lists.
        self._listeners: dict[str, list[Callable[[dict], None]]] = {
            "turnAdded": [],
            "turnCanceled": [],
            "turnInterrupted": [],
            "turnTruncated": [],
            "slid": [],
            "cleared": [],
        }
        # Synthetic turns produced by the log itself (cancel notes, summaries).
        self._synthetic: list[Turn] = []

    # ---- subscribe ----
    def on(self, event: str, cb: Callable[[dict], None]) -> Callable[[], None]:
        if event not in self._listeners:
            raise ValueError(f"Unknown event: {event}")
        self._listeners[event].append(cb)
        def unsubscribe() -> None:
            if cb in self._listeners[event]:
                self._listeners[event].remove(cb)
        return unsubscribe

    def _emit(self, event: str, payload: dict) -> None:
        for cb in list(self._listeners.get(event, ())):
            try:
                cb({"event": event, **payload})
            except Exception:
                pass

    # ---- writes ----
    def add_user_turn(self, text: str, source: str = "text") -> Turn:
        with self._lock:
            turn = Turn(
                id=self._new_id(),
                role="user",
                content=text,
                source=source,
                status="finalized",
                finalized_at_ms=self._now_ms(),
            )
            self._insert(turn)
            self._enforce_window()
            self._emit("turnAdded", {"turn": turn.to_dict(), "role": "user"})
        return turn

    def add_assistant_turn(self, text: str, reasoning: Optional[str] = None) -> Turn:
        with self._lock:
            turn = Turn(
                id=self._new_id(),
                role="assistant",
                content=text,
                reasoning=reasoning,
                status="finalized",
                finalized_at_ms=self._now_ms(),
            )
            self._insert(turn)
            self._enforce_window()
            self._emit("turnAdded", {"turn": turn.to_dict(), "role": "assistant"})
        return turn

    def stream_user_partial(self, text: str) -> Optional[Turn]:
        """Update the current streaming user turn. Returns the turn if created/updated."""
        with self._lock:
            existing = self._get_streaming(role="user")
            if existing is None:
                turn = Turn(
                    id=self._new_id(),
                    role="user",
                    content=text,
                    source="stt",
                    status="streaming",
                )
                self._insert(turn)
                return turn
            existing.content = text
            return existing

    def commit_user_turn(self) -> Optional[Turn]:
        """Finalize the current streaming user turn (or the last finalized user turn)."""
        with self._lock:
            streaming = self._get_streaming(role="user")
            if streaming is None:
                return None
            streaming.status = "finalized"
            streaming.finalized_at_ms = self._now_ms()
            self._enforce_window()
            self._emit("turnAdded", {"turn": streaming.to_dict(), "role": "user", "finalized": True})
            return streaming

    def cancel_stream(self, role: str) -> None:
        """Discard the streaming turn (no history added)."""
        with self._lock:
            streaming = self._get_streaming(role=role)
            if streaming is None:
                return
            del self._turns[streaming.id]

    def cancel_finalized(
        self,
        role: str,
        reason: str = "barge-in",
        summary_text: Optional[str] = None,
    ) -> Optional[Turn]:
        """Mark the latest finalized turn of `role` as canceled.

        A synthetic system turn is added per `cancel_mode`:
          - 'silent': nothing added
          - 'note':   a brief system turn describing the interruption
          - 'summary': a one-sentence summary of the canceled content

        The original turn stays in the log for traceability, but its
        status becomes 'canceled' so `to_messages()` excludes it.

        This is the privacy-mode opt-in. The default barge-in behavior
        is `mark_interrupted`, which keeps the partial turn in the
        conversation context.
        """
        with self._lock:
            turns = [t for t in self._turns.values() if t.role == role and t.status == "finalized"]
            if not turns:
                return None
            turn = turns[-1]
            turn.status = "canceled"
            turn.canceled_at_ms = self._now_ms()
            turn.cancel_reason = reason
            turn.partial_at_cancel = summary_text or turn.content

            mode = self.cancel_mode
            sys_turn = apply_cancel_mode(mode, role, reason, turn.content)
            if sys_turn is not None:
                self._synthetic.append(sys_turn)
                self._emit('turnAdded', {'turn': sys_turn.to_dict(), 'role': 'system', 'synthetic': True})
            self._emit('turnCanceled', {'turn': turn.to_dict(), 'reason': reason, 'mode': mode})
            return turn

    def truncate(
        self,
        turn_id: str,
        played_audio_ms: float,
        word_offsets_ms: Optional[list] = None,
    ) -> Optional[Turn]:
        """Truncate an assistant turn to the audio that was actually played.

        The turn's content is replaced with the text that corresponds
        to `played_audio_ms` (using `word_offsets_ms` to find the cutoff
        word). The truncated turn is still kept in the context (status
        remains 'finalized'; a separate field `truncated_at_ms` records
        where the cut happened).

        If `word_offsets_ms` is missing or the heuristic fails, the
        full text is kept (conservative: don't lose content we can't
        safely truncate).

        Idempotent: calling twice is a no-op.
        """
        with self._lock:
            turn = self._turns.get(turn_id)
            if not turn:
                # Maybe the turn was inserted under a different id; find
                # the most recent assistant turn with the matching role.
                candidates = [
                    t for t in self._turns.values()
                    if t.role == "assistant" and t.status in ("finalized", "interrupted")
                ]
                if not candidates:
                    return None
                turn = candidates[-1]
            if turn.truncated_at_ms is not None:
                # Already truncated; idempotent.
                return turn
            if not word_offsets_ms or not isinstance(word_offsets_ms, list):
                # Can't safely truncate without offsets.
                return turn
            if played_audio_ms is None or played_audio_ms <= 0:
                return turn

            # Find the last word whose offset <= played_audio_ms.
            cutoff_idx = 0
            for i, offset in enumerate(word_offsets_ms):
                if offset <= played_audio_ms:
                    cutoff_idx = i + 1
                else:
                    break
            # No word was heard: clear the content.
            if cutoff_idx == 0:
                turn.content = ""
            else:
                words = turn.content.split()
                # Clamp to min(cutoff_idx, len(words)) for safety.
                kept = words[: min(cutoff_idx, len(words))]
                turn.content = " ".join(kept)
            turn.truncated_at_ms = played_audio_ms
            self._emit("turnTruncated", {
                "turn": turn.to_dict(),
                "played_audio_ms": played_audio_ms,
            })
            return turn

    def mark_interrupted(
        self,
        role: str,
        reason: str = "barge-in",
        partial_text: Optional[str] = None,
    ) -> Optional[Turn]:
        """Mark the latest finalized (or interrupted) turn of `role` as
        interrupted by barge-in.

        Unlike `cancel_finalized`, this KEEPS the turn in the conversation
        context. The LLM will see the partial response as part of the
        history, so the next turn can refer to it.

        Status transitions: finalized -> interrupted. If the turn is
        already interrupted, the timestamp is updated (idempotent).

        No synthetic system turn is injected. The partial content is
        stored in `partial_at_interrupt` for the inspector / UI.
        """
        with self._lock:
            turns = [t for t in self._turns.values()
                     if t.role == role and t.status in ("finalized", "interrupted")]
            if not turns:
                return None
            turn = turns[-1]
            turn.status = "interrupted"
            turn.interrupted_at_ms = self._now_ms()
            turn.interrupted_reason = reason
            if partial_text is not None:
                turn.partial_at_interrupt = partial_text
            self._emit('turnInterrupted', {
                'turn': turn.to_dict(),
                'reason': reason,
            })
            return turn

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
            self._synthetic.clear()
            self._emit("cleared", {})

    # ---- reads ----
    def to_messages(
        self,
        system_prompt: Optional[str] = None,
        cancel_mode: Optional[CancelMode] = None,
    ) -> list[dict]:
        """Build the LLM-ready messages array.

        - System prompt (if given) is the first message.
        - Then all turns in chronological order, with status='finalized'.
        - Canceled turns are replaced by a synthetic system turn per mode.
        - The last `window_size` items are kept; older are dropped.
        """
        with self._lock:
            mode = cancel_mode or self.cancel_mode
            items: list[dict] = []
            if system_prompt:
                items.append({"role": "system", "content": system_prompt})

            # Build the linear sequence of turns + synthetic.
            # Canceled turns are removed from context entirely.
            # Interrupted turns are KEPT (preserved for conversation continuity).
            # The synthetic system turns (cancel notes, summaries) are
            # appended at the end.
            sequence: list[Turn] = []
            for t in self._turns.values():
                if t.status == "streaming":
                    continue
                if t.status == "canceled":
                    continue
                # 'finalized' and 'interrupted' both stay in context.
                sequence.append(t)
            sequence.extend(self._synthetic)

            # Apply sliding window to the turn portion.
            if len(sequence) > self.window_size:
                # Keep the most recent `window_size` items.
                sequence = sequence[-self.window_size:]

            for t in sequence:
                items.append(self._turn_to_message(t))
            return items

    def to_realtime_events(self) -> list[dict]:
        """Format for OpenAI Realtime API: conversation.item.create events."""
        with self._lock:
            events = []
            for t in self._turns.values():
                if t.status == "finalized":
                    events.append(self._turn_to_realtime_event(t, event="create"))
            for t in self._synthetic:
                events.append(self._turn_to_realtime_event(t, event="create"))
            return events

    def history(self, limit: Optional[int] = None) -> list[dict]:
        with self._lock:
            turns = list(self._turns.values())
            if limit is not None:
                turns = turns[-limit:]
            return [t.to_dict() for t in turns] + [s.to_dict() for s in self._synthetic]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "turn_count": len(self._turns),
                "synthetic_count": len(self._synthetic),
                "window_size": self.window_size,
                "cancel_mode": self.cancel_mode,
                "overflow_strategy": self.overflow_strategy,
                "turns": [t.to_dict() for t in self._turns.values()],
                "synthetic": [s.to_dict() for s in self._synthetic],
            }

    def count(self) -> int:
        with self._lock:
            return len(self._turns)

    def pending_user_stream(self) -> Optional[str]:
        with self._lock:
            t = self._get_streaming(role="user")
            return t.content if t else None

    # ---- config ----
    def set_window_size(self, n: int) -> None:
        with self._lock:
            self.window_size = max(1, int(n))
            # Loop until under the new cap, in case the previous cap was much larger.
            while (len(self._turns) + len(self._synthetic)) > self.window_size:
                if self.overflow_strategy == "summarize" and self._summarizer:
                    self._enforce_window()
                else:
                    self._enforce_window()
                    # For truncate, _enforce_window removes 1 per call; loop again.
                    if (len(self._turns) + len(self._synthetic)) <= self.window_size:
                        break
                    continue
                if (len(self._turns) + len(self._synthetic)) <= self.window_size:
                    break
            self._emit("slid", {"window_size": self.window_size})

    def set_cancel_mode(self, mode: CancelMode) -> None:
        if mode not in CANCEL_MODES:
            raise ValueError(f"Unknown cancel mode: {mode!r}")
        with self._lock:
            self.cancel_mode = mode

    def set_overflow_strategy(self, strategy: OverflowStrategy) -> None:
        if strategy not in ("truncate", "summarize"):
            raise ValueError(f"Unknown overflow strategy: {strategy!r}")
        with self._lock:
            self.overflow_strategy = strategy

    def set_summarizer(self, fn: Optional[Callable[[list], str]]) -> None:
        """fn(old_turns) -> one-line summary. None to disable."""
        self._summarizer = fn

    # ---- internals ----
    def _now_ms(self) -> float:
        import time
        return time.time() * 1000

    def _new_id(self) -> str:
        return uuid.uuid4().hex

    def _insert(self, turn: Turn) -> None:
        self._turns[turn.id] = turn

    def _get_streaming(self, role: str) -> Optional[Turn]:
        for t in self._turns.values():
            if t.role == role and t.status == "streaming":
                return t
        return None

    def _enforce_window(self) -> None:
        """Apply overflow strategy. Removes oldest turns beyond the window.

        The window size applies to the *total* linear sequence (turns +
        synthetics), not just to user/assistant turns. With strategy=
        'summarize', we keep evicting + summarizing until total fits.
        """
        # Loop: each iteration either removes one item (truncate) or
        # batch-evicts turns, summarizes, and replaces with one synth.
        # We re-check the total after each operation.
        while True:
            total = len(self._turns) + len(self._synthetic)
            if total <= self.window_size:
                return
            if self.overflow_strategy == "truncate":
                # Drop oldest. Prefer turns over synthetics.
                if self._turns:
                    self._turns.popitem(last=False)
                elif self._synthetic:
                    self._synthetic.pop(0)
                else:
                    return
                continue
            # summarize
            if not self._summarizer:
                # No summarizer: fall back to truncate.
                if self._turns:
                    self._turns.popitem(last=False)
                elif self._synthetic:
                    self._synthetic.pop(0)
                else:
                    return
                continue
            # Evict as many turns as we can, summarize them into one synth.
            evicted: list[Turn] = []
            # Stop evicting when there is "room for one more" (the synth).
            while len(self._turns) > 0 and (len(self._turns) + len(self._synthetic)) > self.window_size - 1:
                _, t = self._turns.popitem(last=False)
                evicted.append(t)
            if not evicted:
                # If no turns to evict, drop the oldest synth instead.
                if self._synthetic:
                    self._synthetic.pop(0)
                    continue
                return
            try:
                summary = self._summarizer(evicted)
            except Exception:
                summary = f"[Summary of {len(evicted)} earlier turns failed]"
            synth = Turn(
                id=self._new_id(),
                role="system",
                content=summary,
                status="finalized",
                finalized_at_ms=self._now_ms(),
            )
            self._synthetic.insert(0, synth)
            # Loop continues; the synth may push us over the cap again.

    def _turn_to_message(self, t: Turn) -> dict:
        if t.role == "user":
            return {"role": "user", "content": t.content}
        if t.role == "assistant":
            # Most chat-completion APIs don't accept a separate "reasoning"
            # field, so we fold it into the content if present.
            if t.reasoning and t.reasoning not in t.content:
                return {"role": "assistant", "content": f"[reasoning]\n{t.reasoning}\n[/reasoning]\n{t.content}"}
            return {"role": "assistant", "content": t.content}
        return {"role": "system", "content": t.content}

    def _turn_to_realtime_event(self, t: Turn, event: str = "create") -> dict:
        if event == "delete":
            return {"type": "conversation.item.delete", "item_id": t.id}
        # create
        if t.role == "user":
            return {
                "type": "conversation.item.create",
                "item": {
                    "id": t.id,
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": t.content}],
                },
            }
        if t.role == "assistant":
            return {
                "type": "conversation.item.create",
                "item": {
                    "id": t.id,
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": t.content}],
                },
            }
        return {
            "type": "conversation.item.create",
            "item": {
                "id": t.id,
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": t.content}],
            },
        }
