"""Tests for the pure fan speed-table helpers (custom_components/.../fan_speeds.py).

The module has no Home Assistant dependency, so we load it directly by file
path (importing the package would pull in HA).
"""
import importlib.util
from pathlib import Path

import pytest

_FS_PATH = (
    Path(__file__).parent.parent
    / "custom_components" / "zencontrol" / "fan_speeds.py"
)
_spec = importlib.util.spec_from_file_location("zc_fan_speeds", _FS_PATH)
fan_speeds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fan_speeds)


# --- default generation ----------------------------------------------------

def test_default_three_speeds():
    assert fan_speeds.default_fan_speeds(3) == [
        {"name": "Low", "level": 85},
        {"name": "Medium", "level": 169},
        {"name": "High", "level": 254},
    ]


def test_default_four_speeds_top_is_max():
    speeds = fan_speeds.default_fan_speeds(4)
    assert [s["name"] for s in speeds] == ["Low", "Medium", "High", "Max"]
    assert speeds[-1]["level"] == 254
    assert [s["level"] for s in speeds] == sorted(s["level"] for s in speeds)


def test_default_uncommon_count_uses_generic_names():
    speeds = fan_speeds.default_fan_speeds(5)
    assert [s["name"] for s in speeds] == [f"Speed {i}" for i in range(1, 6)]
    assert speeds[-1]["level"] == 254


# --- parse / format round-trip --------------------------------------------

def test_parse_valid_table():
    assert fan_speeds.parse_fan_speeds("Low:85, Medium:169, High:254") == [
        {"name": "Low", "level": 85},
        {"name": "Medium", "level": 169},
        {"name": "High", "level": 254},
    ]


def test_parse_format_roundtrip():
    text = "Low:85, Medium:169, High:254"
    assert fan_speeds.format_fan_speeds(fan_speeds.parse_fan_speeds(text)) == text


@pytest.mark.parametrize(
    "bad",
    [
        "",                      # empty
        "Low",                   # no level
        "Low:0",                 # below range
        "Low:255",               # above range
        "High:254, Low:85",      # not ascending
        "Low:85, Low:170",       # duplicate name
        "Low:85, Medium:85",     # duplicate level
    ],
)
def test_parse_rejects_bad_tables(bad):
    with pytest.raises(ValueError):
        fan_speeds.parse_fan_speeds(bad)
