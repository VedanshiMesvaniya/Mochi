from app.ai.reasoning_budget import is_trivial_chat


def test_common_acknowledgments_are_trivial():
    for text in ("ok", "Okay!", "thanks", "thank you.", "lol", "np", "got it", "sure"):
        assert is_trivial_chat(text), f"{text!r} should be trivial"


def test_punctuation_and_casing_do_not_matter():
    assert is_trivial_chat("Thanks!!")
    assert is_trivial_chat("  ok  ")
    assert is_trivial_chat("LOL")


def test_ordinary_questions_are_not_trivial():
    for text in (
        "what's the weather like today",
        "remind me to call mom",
        "i'm sad",
        "it broke again",
        "can you help me plan my week",
    ):
        assert not is_trivial_chat(text), f"{text!r} should not be trivial"


def test_unfamiliar_short_phrase_is_not_assumed_trivial():
    """Only the fixed known-empty-content list counts - an unfamiliar
    short reply must never be silently treated as trivial."""
    assert not is_trivial_chat("maybe")
    assert not is_trivial_chat("why")
