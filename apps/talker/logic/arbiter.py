"""Arbiter: decides *when* thinker events may interrupt the conversation — in code, never by the LLM.

Rules:
  * priority: question > failure > completion > delivery/accepted > progress
  * speak only if: bot idle, user silent ≥ min_user_silence, no end-of-utterance pending, not dictating,
    and the proactive cooldown has passed (questions/failures may bypass the cooldown, never the silence rule)
  * progress is coalesced per task (latest wins) and goes stale quickly; completions/failures never
    expire silently — if they can't be voiced, they surface as `must_mention` in the state block
  * an event counts as delivered only after its audio actually played (mark_spoken)
  * one open thinker question at a time
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from protocol.messages import Event

ALWAYS_SURFACE = {"task.question", "task.failed", "task.completed", "task.cancelled", "delivery.failed",
                  "command.rejected"}
PROGRESS_STALE_S = 20.0


@dataclass
class Signals:
    now: float
    bot_speaking: bool = False
    user_speaking: bool = False
    eou_pending: bool = False        # turn detector still deciding whether the user finished
    mode: str = "converse"           # converse | dictate
    last_user_speech_end: float = 0.0


@dataclass
class _Pending:
    event: Event
    offered_at: float
    in_flight: bool = False
    interrupted: bool = False


@dataclass
class Arbiter:
    min_user_silence_s: float = 1.2
    proactive_cooldown_s: float = 8.0
    progress_cooldown_s: float = 10.0
    progress_not_before: float = 0.0     # progress only after BolDoc has been quiet a while (set by the session)
    _pending: Dict[str, _Pending] = field(default_factory=dict)
    _last_proactive_at: float = -1e9
    _last_progress_at: float = -1e9
    _open_question: Optional[str] = None  # event_id of a question voiced but not yet answered

    # ------------------------------------------------------------------ intake
    def offer(self, event: Event, now: float) -> None:
        if event.type == "task.progress":  # coalesce: only the latest progress per task matters
            for eid, p in list(self._pending.items()):
                if p.event.type == "task.progress" and p.event.task_id == event.task_id and not p.in_flight:
                    del self._pending[eid]
        self._pending[event.event_id] = _Pending(event, now)

    def question_resolved(self) -> None:
        self._open_question = None

    def drop_questions(self) -> None:
        """The writer has its answer or stopped waiting: a question not yet voiced is never asked."""
        self._open_question = None
        for eid, p in list(self._pending.items()):
            if p.event.type == "task.question" and not p.in_flight:
                del self._pending[eid]

    # ------------------------------------------------------------------ decision
    def _expire(self, now: float) -> None:
        for eid, p in list(self._pending.items()):
            ev = p.event
            if ev.type in ALWAYS_SURFACE:
                continue
            stale = ev.type == "task.progress" and now - p.offered_at > PROGRESS_STALE_S
            if stale or (ev.expires_at is not None and now > ev.expires_at):
                del self._pending[eid]

    def _gate_open(self, s: Signals) -> bool:
        return (not s.bot_speaking and not s.user_speaking and not s.eou_pending and s.mode != "dictate"
                and s.now - s.last_user_speech_end >= self.min_user_silence_s)

    def decide(self, s: Signals) -> Optional[Event]:
        """Return the single event to voice proactively right now, or None."""
        self._expire(s.now)
        if not self._gate_open(s) or any(p.in_flight for p in self._pending.values()):
            return None
        candidates = sorted((p for p in self._pending.values() if not p.interrupted),
                            key=lambda p: (p.event.priority, p.event.seq))
        for p in candidates:
            ev = p.event
            urgent = ev.priority <= 1  # questions, failures, rejections
            if ev.type == "task.question" and self._open_question is not None:
                continue  # one open question at a time
            if not urgent and s.now - self._last_proactive_at < self.proactive_cooldown_s:
                continue
            if ev.type == "task.progress" and (s.now - self._last_progress_at < self.progress_cooldown_s
                                               or s.now < self.progress_not_before):
                continue
            p.in_flight = True
            self._last_proactive_at = s.now
            if ev.type == "task.progress":
                self._last_progress_at = s.now
            return ev
        return None

    # ------------------------------------------------------------------ playout feedback
    def mark_spoken(self, event_id: str) -> None:
        """Audio fully played (Plivo playedStream checkpoint): the caller has heard it."""
        p = self._pending.pop(event_id, None)
        if p is not None and p.event.type == "task.question":
            self._open_question = event_id

    def mark_interrupted(self, event_id: str) -> None:
        """Caller barged in before the end: not heard -> fold into the next turn instead of re-voicing now."""
        p = self._pending.get(event_id)
        if p is not None:
            p.in_flight, p.interrupted = False, True

    def mentioned_in_turn(self, event_ids: List[str]) -> None:
        """The LLM's normal reply covered these must-mention events (and the reply finished playing)."""
        for eid in event_ids:
            self.mark_spoken(eid)

    def must_mention(self) -> List[Event]:
        """Events the next normal reply must cover (interrupted or important-but-not-yet-voiced)."""
        out = [p.event for p in self._pending.values()
               if p.interrupted or (p.event.type in ALWAYS_SURFACE and not p.in_flight)]
        return sorted(out, key=lambda e: (e.priority, e.seq))
