"""ConversationStateMachine — explicit lifecycle of a conversation turn.

Distinct from MessageStateMachine (which tracks the bus message):
this machine tracks the *semantic* lifecycle of a turn that ends up
in the ConversationLog.

States:
  IDLE              no active turn
  STREAMING         LLM is generating tokens
  DELIVERED         assistant finished speaking (audio played) OR
                    user input finalized
  COMMITTED         added to ConversationLog (irreversible)
  CANCELED          turn was canceled before reaching DELIVERED

Only COMMITTED turns are visible to `to_messages()`.
"""
from __future__ import annotations

from server.shared.state.machine import StateMachine

# Constants matching the JS mirror in client/src/state/machine.js
CREATED = "created"
THINKING = "thinking"
SPEAKING = "speaking"
INTERRUPTING = "interrupting"
INTERRUPTED = "interrupted"
COMPLETED = "completed"
FAILED = "failed"

RunState = {
    "CREATED": CREATED,
    "THINKING": THINKING,
    "SPEAKING": SPEAKING,
    "INTERRUPTING": INTERRUPTING,
    "INTERRUPTED": INTERRUPTED,
    "COMPLETED": COMPLETED,
    "FAILED": FAILED,
}


class ConversationStateMachine(StateMachine):
    STATES = {
        "IDLE":       {"transitions": {"STREAMING"}},
        "STREAMING":  {"transitions": {"DELIVERED", "CANCELED"}},
        "DELIVERED":  {"transitions": {"COMMITTED", "CANCELED"}},
        "COMMITTED":  {"transitions": set()},  # terminal
        "CANCELED":   {"transitions": {"IDLE"}},
    }
    INITIAL = "IDLE"
