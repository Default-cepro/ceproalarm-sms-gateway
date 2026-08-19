from src.core.parser import parse_response


def test_case_insensitive_substring_match():
    assert parse_response("GPRS: Link Up", "gprs: link up")
    assert parse_response("gprs: link up", "GPRS: LINK UP")
    assert parse_response("  GPRS: Link Up  ", "gprs: link up")


def test_non_match_returns_false():
    assert not parse_response("GPRS: Link Down", "Online")
    assert not parse_response("", "Online")


def test_empty_expected_matches_any_response():
    # Current behavior: an empty expected pattern matches any response.
    assert parse_response("anything at all", "")
    assert parse_response("", "")


def test_empty_response_with_expected_returns_false():
    assert not parse_response("", "Online")
    assert not parse_response(None, "Online")
