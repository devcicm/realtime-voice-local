"""State facade — single import."""
from .machine import StateMachine, InvalidTransitionError, TransitionRecord
from .machines import (
    ConnectionStateMachine,
    TurnStateMachine,
    PipelineStateMachine,
    MessageStateMachine,
)

__all__ = [
    "StateMachine", "InvalidTransitionError", "TransitionRecord",
    "ConnectionStateMachine",
    "TurnStateMachine",
    "PipelineStateMachine",
    "MessageStateMachine",
]
