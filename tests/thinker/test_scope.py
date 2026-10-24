"""Long documents: the catalogue's outlines and the scope check's answer, as BolDoc uses them."""
import pytest

from apps.thinker.catalogue import KINDS, UNDERSHOOT, WORDS_PER_PAGE, outline_for
from apps.thinker.scope import _parse


def test_an_outline_comes_from_the_type_or_from_the_caller_with_its_lengths():
    outline, size, unit = outline_for("project_report")
    assert [s["title"] for s in outline] == list(KINDS["project_report"].outline) and (size, unit) == (12, "pages")
    assert [s["id"] for s in outline][:3] == ["s1", "s2", "s3"]
    assert outline[0]["words"] == int(12 * WORDS_PER_PAGE * UNDERSHOOT / 9)  # aimed above: the writer undershoots
    mine, size, _ = outline_for("business_plan", ["Market", "Money", "Team"], 6)  # the caller's own sections
    assert [s["title"] for s in mine] == ["Market", "Money", "Team"] and size == 6
    deck, n, unit = outline_for("pitch_deck", None, 15)
    assert unit == "slides" and n == 15 and len(deck) == 12  # 15 slides spread over its 12 sections
    other, _, _ = outline_for("other")
    assert other[0]["title"] == "Introduction"


def test_the_scope_answer_is_checked_and_anything_unclear_is_short():
    assert _parse({"size": "long", "kind": "pitch_deck", "title": "Startup deck", "length": "10",
                   "sections": ["Problem", "Team"]}) == {
        "size": "long", "kind": "pitch_deck", "title": "Startup deck", "length": 10, "sections": ["Problem", "Team"]}
    assert _parse({"size": "long", "kind": "poem_book", "length": 999})["kind"] == "other"  # unknown type: other
    assert _parse({"size": "long", "kind": "poem_book", "length": 999})["length"] is None
    short = _parse({"size": "short", "kind": "project_report", "title": "x", "length": 5, "sections": ["a"]})
    assert short == {"size": "short", "kind": "", "title": "", "length": None, "sections": []}
    with pytest.raises(ValueError):
        _parse({"size": "medium"})  # asked again once by chat_json; a failed check is "short"
