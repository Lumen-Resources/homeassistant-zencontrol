"""Pure helpers for fan speed tables — no Home Assistant dependency.

Kept separate from config_flow so the parsing/validation can be unit-tested
without importing Home Assistant. A speed table is a list of
``{"name": str, "level": int}`` ordered ascending by level (arc level 1–254).
"""
from __future__ import annotations

_FAN_SPEED_NAMES = {
    2: ["Low", "High"],
    3: ["Low", "Medium", "High"],
    4: ["Low", "Medium", "High", "Max"],
}


def default_fan_speeds(count: int) -> list[dict]:
    """Generate an evenly-spaced speed table (top speed at max arc level)."""
    names = _FAN_SPEED_NAMES.get(count, [f"Speed {i + 1}" for i in range(count)])
    return [
        {"name": names[i], "level": round(254 * (i + 1) / count)}
        for i in range(count)
    ]


def parse_fan_speeds(text: str) -> list[dict]:
    """Parse a "Name:level, Name:level" string into a validated speed table.

    Raises ValueError on malformed input, out-of-range level, duplicate name,
    or non-ascending / non-distinct levels.
    """
    speeds: list[dict] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, level_str = part.rpartition(":")
        if not sep or not name.strip():
            raise ValueError("each entry must be 'Name:level'")
        level = int(level_str.strip())
        if not 1 <= level <= 254:
            raise ValueError("levels must be 1-254")
        speeds.append({"name": name.strip(), "level": level})
    if not speeds:
        raise ValueError("at least one speed is required")
    names = [s["name"] for s in speeds]
    if len(names) != len(set(names)):
        raise ValueError("speed names must be unique")
    levels = [s["level"] for s in speeds]
    if levels != sorted(levels) or len(levels) != len(set(levels)):
        raise ValueError("levels must be ascending and distinct")
    return speeds


def format_fan_speeds(speeds: list[dict]) -> str:
    """Render a speed table back to the editable 'Name:level, …' string."""
    return ", ".join(f"{s['name']}:{s['level']}" for s in speeds)
