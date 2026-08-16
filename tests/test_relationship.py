from app.memory import relationship


def test_record_interaction_increments(temp_db):
    assert relationship.record_interaction() == 1
    assert relationship.record_interaction() == 2
    assert relationship.record_interaction() == 3
    assert relationship.get_interaction_count() == 3


def test_level_for_count_boundaries():
    assert relationship.level_for_count(0) == relationship.NEW
    assert relationship.level_for_count(4) == relationship.NEW
    assert relationship.level_for_count(5) == relationship.GETTING_TO_KNOW
    assert relationship.level_for_count(24) == relationship.GETTING_TO_KNOW
    assert relationship.level_for_count(25) == relationship.FAMILIAR
    assert relationship.level_for_count(1000) == relationship.FAMILIAR


def test_get_interaction_count_defaults_to_zero(temp_db):
    assert relationship.get_interaction_count() == 0
