"""Redis channel names shared by the talker and the control plane."""


def events_channel(session_id: str) -> str:
    """Durable session events (task.*, delivery.*), published by the control plane after they are stored."""
    return f"boldoc:events:{session_id}"


def live_channel(session_id: str) -> str:
    """Live call feed (caller/BolDoc text, agent state), published by the talker, for the website's call view."""
    return f"boldoc:live:{session_id}"


# Calls in progress, for admission control: a sorted set of call UUID -> last heartbeat (unix seconds). Control adds a
# call when it admits it (reserving the slot); the talker refreshes it every HEARTBEAT_S while the stream runs and
# removes it at the end. A call whose heartbeat is older than PRESENCE_TTL_S no longer counts (talker crashed).
ACTIVE_CALLS = "boldoc:calls:active"
HEARTBEAT_S = 10
PRESENCE_TTL_S = 30
# Which LLM each call in progress uses ("primary" or "overflow"): a hash call UUID -> name, cleared with the call.
CALL_LLM = "boldoc:calls:llm"
