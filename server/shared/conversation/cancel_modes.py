"""Cancel modes — what to inject when a turn is canceled mid-delivery.

Three modes:
  - 'silent':   nothing is added. The canceled turn is excluded from
                 `to_messages()` and no synthetic turn is produced.
  - 'note':     a brief system turn describes the interruption, with up
                 to 80 chars of the partial content. Default mode.
  - 'summary':  same shape as 'note' but the partial content is
                 summarized to one sentence via a cheap heuristic
                 (first sentence, max 120 chars).

Each `apply_cancel_mode` returns either a `Turn` (to be appended to
synthetic turns) or `None` (no injection).
"""
from __future__ import annotations

from .turn import Turn

CANCEL_MODES = ("silent", "note", "summary")


def _truncate_words(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    # Cut at last word boundary before max_chars
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut + "..."


def _first_sentence(text: str) -> str:
    """Extract the first sentence. Heuristic: split on '.', '!', '?'."""
    import re
    m = re.search(r"[.!?]", text)
    if not m:
        return text
    return text[: m.end()].strip()


def make_note(role: str, reason: str, partial: str) -> Turn:
    preview = _truncate_words(partial or "", 80)
    content = (
        f"[Previous {role} response was interrupted by the user "
        f"(reason: {reason}). Partial content: \"{preview}\"]"
    )
    return Turn(id=_new_id(), role="system", content=content, status="finalized")


def make_summary(role: str, reason: str, partial: str) -> Turn:
    summary_text = _first_sentence(partial or "")
    summary_text = _truncate_words(summary_text, 120)
    content = (
        f"[Previous {role} response was interrupted by the user "
        f"(reason: {reason}). Summary: \"{summary_text}\"]"
    )
    return Turn(id=_new_id(), role="system", content=content, status="finalized")


def make_silent(*_args, **_kwargs) -> None:
    return None


def _new_id() -> str:
    import secrets
    return secrets.token_hex(8)


# Dispatch table.
APPLIERS = {
    "silent":  make_silent,
    "note":    make_note,
    "summary": make_summary,
}


def apply_cancel_mode(mode: str, role: str, reason: str, partial: str):
    """Returns a Turn to inject, or None."""
    if mode not in APPLIERS:
        return None
    return APPLIERS[mode](role, reason, partial or "")
