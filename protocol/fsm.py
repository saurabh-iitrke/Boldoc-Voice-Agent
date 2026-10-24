"""State machines enforced in code (never left to the LLM's judgement).

Conversation states also gate which talker tools are exposed: the LLM cannot call a tool its
current state does not allow (e.g. no `deliver` while still dictating).
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


TERMINAL: FrozenSet[TaskState] = frozenset({TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED,
                                            TaskState.SUPERSEDED})

_TASK_TRANSITIONS: Dict[TaskState, FrozenSet[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.SUPERSEDED}),
    TaskState.RUNNING: frozenset({TaskState.AWAITING_INPUT, TaskState.COMMITTING, TaskState.QUEUED,  # requeue on crash
                                  TaskState.FAILED, TaskState.CANCELLED, TaskState.SUPERSEDED}),
    TaskState.AWAITING_INPUT: frozenset({TaskState.RUNNING, TaskState.QUEUED, TaskState.CANCELLED,
                                         TaskState.FAILED, TaskState.SUPERSEDED}),
    TaskState.COMMITTING: frozenset({TaskState.COMPLETED, TaskState.QUEUED, TaskState.FAILED}),
}


class InvalidTransition(ValueError):
    pass


def check_task_transition(current: TaskState, new: TaskState) -> None:
    if new not in _TASK_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"task {current.value} -> {new.value} not allowed")


class ConvState(str, Enum):
    DISCLOSE = "disclose"
    CONVERSE = "converse"
    DICTATE = "dictate"
    CONFIRM = "confirm"
    ENGAGE = "engage"          # thinker working; talker keeps the caller usefully engaged
    ASK = "ask"                # relaying a thinker question
    REVIEW = "review"
    DELIVER = "deliver"
    CLOSE = "close"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"        # caller gone; work continues, delivery is async


_CONV_TRANSITIONS: Dict[ConvState, FrozenSet[ConvState]] = {
    ConvState.DISCLOSE: frozenset({ConvState.CONVERSE}),
    ConvState.CONVERSE: frozenset({ConvState.DICTATE, ConvState.CONFIRM, ConvState.ENGAGE, ConvState.REVIEW,
                                   ConvState.DELIVER, ConvState.CLOSE}),
    ConvState.DICTATE: frozenset({ConvState.CONVERSE, ConvState.CONFIRM}),
    ConvState.CONFIRM: frozenset({ConvState.CONVERSE, ConvState.DICTATE, ConvState.ENGAGE}),
    ConvState.ENGAGE: frozenset({ConvState.ASK, ConvState.REVIEW, ConvState.CONVERSE, ConvState.DICTATE}),
    ConvState.ASK: frozenset({ConvState.ENGAGE, ConvState.REVIEW}),
    ConvState.REVIEW: frozenset({ConvState.ENGAGE, ConvState.CONVERSE, ConvState.DICTATE, ConvState.DELIVER,
                                 ConvState.CLOSE}),
    ConvState.DELIVER: frozenset({ConvState.REVIEW, ConvState.CONVERSE, ConvState.CLOSE}),
    ConvState.CLOSE: frozenset(),
}
_ANYWHERE = frozenset({ConvState.RECONNECTING, ConvState.OFFLINE})


def check_conv_transition(current: ConvState, new: ConvState) -> None:
    if current == ConvState.CLOSE:
        raise InvalidTransition("conversation already closed")
    if new in _ANYWHERE or current in _ANYWHERE:
        return  # disconnects can happen anywhere; resume returns to the saved state
    if new not in _CONV_TRANSITIONS[current]:
        raise InvalidTransition(f"conversation {current.value} -> {new.value} not allowed")


# Tools the talker LLM may call in each state (everything else is hidden from the model).
_BASE = frozenset({"get_status", "set_mode", "end_call"})
TOOLS_BY_STATE: Dict[ConvState, FrozenSet[str]] = {
    ConvState.DISCLOSE: frozenset({"end_call"}),
    ConvState.CONVERSE: _BASE | {"create_doc", "edit_doc", "undo", "list_documents", "open_document",
                                 "deliver", "verify_recipient", "delete_my_data"},
    ConvState.DICTATE: frozenset({"set_mode", "end_call"}),
    ConvState.CONFIRM: _BASE | {"create_doc", "edit_doc"},
    ConvState.ENGAGE: _BASE | {"edit_doc", "cancel", "verify_recipient", "deliver"},
    ConvState.ASK: _BASE | {"answer", "cancel"},
    ConvState.REVIEW: _BASE | {"edit_doc", "undo", "deliver", "verify_recipient", "cancel"},
    ConvState.DELIVER: _BASE | {"deliver", "verify_recipient", "edit_doc"},
    ConvState.CLOSE: frozenset(),
    ConvState.RECONNECTING: frozenset(),
    ConvState.OFFLINE: frozenset(),
}
