"""The agent's settings for one call: read at the call's start, kept to its end."""
import asyncio
import dataclasses

import pytest

pytest.importorskip("pipecat")  # the talker's session runs inside its Pipecat pipeline

from apps.talker import agent_config
from apps.talker import session as session_mod
from apps.talker.agent_config import CallSettings, from_published
from apps.talker.logic.language import LanguageTracker


def test_the_defaults_are_the_sessions_own_constants():
    c = CallSettings()
    assert (c.reask_after_s, c.idle_check_after_s, c.idle_goodbye_after_s) == (session_mod.REASK_S,
                                                                              session_mod.IDLE_CHECK_S,
                                                                              session_mod.IDLE_BYE_S)
    assert (c.unfinished_hold_s, c.listening_sound_after_s, c.progress_every_s) == (
        session_mod.UNFINISHED_HOLD_S, session_mod.ACK_AFTER_S, session_mod.PROGRESS_EVERY_S)


def test_published_settings_become_a_calls_settings_voices_included():
    c = from_published(7, {"wait_tone": False, "reask_after_s": 9, "default_language": "hinglish",
                           "voice_hinglish": "bulbul:v4-flash/aparna_hi_customer", "price_minute": 5})
    assert c.version == 7 and c.wait_tone is False and c.reask_after_s == 9 and c.default_language == "hinglish"
    assert c.voices["hinglish"] == ("bulbul:v4-flash", "aparna_hi_customer")
    assert c.voices["en-IN"] == CallSettings().voices["en-IN"]  # what wasn't published stays as it was


def test_a_call_runs_with_its_settings_and_never_waits_for_them(monkeypatch):
    from tests.talker.test_session import FakeControl
    s = session_mod.CallSession("s", "919988776655", "pstn", FakeControl())
    s.apply_settings(dataclasses.replace(CallSettings(), version=3, reask_after_s=10, idle_check_after_s=20,
                                         listening_sound=False, wait_tone=False, progress_every_s=90))
    assert s.arbiter.progress_cooldown_s == 90 and s.trace.events[-1]["e"] == "agent_config"
    s.turns_heard, s.last_bot_line, s.last_user_speech_end, s.last_bot_stop = 1, "Kis tareekh se?", 100.0, 100.0
    assert s._idle_notice(100.0 + 9) is None and s._idle_notice(100.0 + 10.5) == "call.reask"
    s.verdict, s.ack_pcm = "incomplete", b"x"
    assert not s.wants_ack(5.0)                      # "हम्म" off
    assert not s._wants_wait_tone(500.0)             # the wait tone off

    async def unreachable(*a, **k):
        raise OSError("control away")
    monkeypatch.setattr(agent_config, "_last", None)

    class Down:
        get = unreachable
    assert asyncio.run(agent_config.for_call(client=Down())) == CallSettings()  # the defaults, at once


def test_a_new_caller_can_start_in_hindi_and_hinglish():
    assert LanguageTracker.starting(None, None, default="hinglish").family == "hinglish"
    assert LanguageTracker.starting(None, None, default="en-IN").family == "en-IN"
    assert LanguageTracker.starting("en-IN", None, default="hinglish").family == "en-IN"  # a website pick wins
    assert LanguageTracker.starting(None, "ta-IN", default="hinglish").current == "ta-IN"  # so does their last call
