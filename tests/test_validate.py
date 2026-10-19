"""Callsign, park reference and grid square validation."""

import pytest

from pota_logger import validate


@pytest.mark.parametrize(
    "text,expected",
    [("w1aw", True), ("K1ABC", True), ("VE3/W1AW/P", True), ("K1ABC/4", True),
     ("9A1ABC", True), ("ab", False), ("ABC", False), ("", False), ("K1ABC!", False)],
)
def test_is_valid_call(text, expected):
    assert validate.is_valid_call(text) is expected


def test_normalize_call_upper_cases_and_trims():
    assert validate.normalize_call("  w1aw  ") == "W1AW"
    assert validate.normalize_call("ve3\\w1aw") == "VE3/W1AW"


@pytest.mark.parametrize(
    "text,expected",
    [("VE3/W1AW/P", "W1AW"), ("W1AW/4", "W1AW"), ("W1AW/P", "W1AW"),
     ("W1AW", "W1AW"), ("k1abc/qrp", "K1ABC")],
)
def test_base_call_strips_portable_designators(text, expected):
    assert validate.base_call(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("US-0001", "US-0001"), ("us0001", "US-0001"), ("k-1234", "K-1234"),
     ("ca-0123", "CA-0123"), (" us-0001 ", "US-0001")],
)
def test_normalize_park(text, expected):
    assert validate.normalize_park(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("US-0001", True), ("us0001", True), ("K-1234", True), ("US-00001", True),
     ("9A-0001", True), ("US-12", False), ("bad", False), ("", False)],
)
def test_is_valid_park(text, expected):
    assert validate.is_valid_park(text) is expected


@pytest.mark.parametrize(
    "text,expected",
    [("fn42", "FN42"), ("FN42BB", "FN42bb"), ("fn42bb11", "FN42bb11"), ("", "")],
)
def test_normalize_grid_uses_conventional_casing(text, expected):
    assert validate.normalize_grid(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("FN42", True), ("fn42", True), ("FN42bb", True), ("FN42bb11", True),
     ("ZZ99", False), ("FN", False), ("", False), ("FN4", False)],
)
def test_is_valid_grid(text, expected):
    assert validate.is_valid_grid(text) is expected
