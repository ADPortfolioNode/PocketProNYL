"""ast.literal_eval replacement keeps parsing behaviour and blocks code execution."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from utils.game_data_parser import _parse_numbers, _parse_pick3_digits  # noqa: E402


def test_list_strings_still_parse():
    assert _parse_numbers("[1, 2, 3]") == [1, 2, 3]
    assert _parse_numbers("05 12 33") == [5, 12, 33]
    assert _parse_pick3_digits("[4, 0, 9]") == [4, 0, 9]


def test_code_is_not_executed():
    payload = "[__import__('os').system('echo pwned')]"
    # Falls back to digit extraction instead of executing anything.
    assert _parse_numbers(payload) == []
