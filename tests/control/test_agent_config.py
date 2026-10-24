"""Agent settings from the admin console: a whitelist, each range-checked, nothing changed until a publish."""
import dataclasses

import pytest

from apps.control import agent_config as ac
from apps.control.accounts.pricing import PRICING


def test_the_defaults_are_todays_values_so_nothing_changes_until_a_publish():
    d = ac.defaults()
    assert d["price_minute"] == PRICING.minute and d["price_doc"] == PRICING.doc_create
    assert d["max_call_minutes"] * 60 == PRICING.max_call_s and d["trial_edits_per_doc"] == PRICING.trial_edits_per_doc
    assert d["unfinished_hold_s"] == 3.5 and d["reask_after_s"] == 6 and d["idle_check_after_s"] == 12
    assert d["voice_hinglish"] == "bulbul:v4-flash/simran_enhi_customer" and d["voice_ta-IN"] == "bulbul:v3/simran"
    assert set(ac.talker_view(0, d)["settings"]) == set(ac.TALKER_KEYS)
    assert "price_minute" not in ac.talker_view(0, d)["settings"]  # prices are control's


@pytest.mark.parametrize("changes,key,says", [
    ({"no_such_setting": 1}, "no_such_setting", "not a setting"),
    ({"price_minute": "five"}, "price_minute", "a number"),
    ({"price_minute": 4.5}, "price_minute", "a whole number"),
    ({"unfinished_hold_s": 9}, "unfinished_hold_s", "between 2 and 5"),
    ({"wait_tone": "yes"}, "wait_tone", "on or off"),
    ({"voice_hinglish": "bulbul:v4-flash/someone_else"}, "voice_hinglish", "not one of the choices"),
    ({"trial_call_minutes": 15, "max_call_minutes": 10}, "trial_call_minutes", "longest call"),
])
def test_values_outside_the_whitelist_or_their_ranges_are_refused(changes, key, says):
    _, errors = ac.validate(ac.defaults(), changes)
    assert key in errors and says in errors[key]


def test_a_publish_changes_prices_and_limits_from_the_next_charge_or_admission():
    before = dataclasses.replace(PRICING)
    try:
        after, errors = ac.validate(ac.defaults(), {"price_minute": 5, "max_call_minutes": 20, "wait_tone": False})
        changed = ac.diff(ac.defaults(), after)
        assert not errors and changed == {"price_minute": [PRICING.minute, 5], "max_call_minutes": [30, 20],
                                          "wait_tone": [True, False]}
        ac.apply_to_control(after)
        assert PRICING.minute == 5 and PRICING.max_call_s == 1200
    finally:
        for f in dataclasses.fields(PRICING):
            setattr(PRICING, f.name, getattr(before, f.name))
