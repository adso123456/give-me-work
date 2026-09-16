from cards.actions import parse_action_index

ACTIONS = ["replied", "later"]


def test_parse_action_index_maps_digits_to_actions():
    assert parse_action_index("1", ACTIONS) == "replied"
    assert parse_action_index(" 2 ", ACTIONS) == "later"
    assert parse_action_index("2", ["resume_accepted", "resume_rejected", "later"]) == "resume_rejected"


def test_parse_action_index_ignores_non_matching_input():
    assert parse_action_index("1", []) is None
    assert parse_action_index("3", ACTIONS) is None
    assert parse_action_index("0", ACTIONS) is None
    assert parse_action_index("/job_action tok replied", ACTIONS) is None
    assert parse_action_index("好的", ACTIONS) is None
    assert parse_action_index("12", ACTIONS) is None
    assert parse_action_index("", ACTIONS) is None
