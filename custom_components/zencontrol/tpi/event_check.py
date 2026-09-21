"""Pure decision logic for whether TPI event delivery must be re-asserted.

Split out from the coordinator so it can be unit tested without a Home
Assistant install: the controller persists the TPI event *mode* byte across
reboots but not the unicast *destination address*, so checking the ENABLED
bit alone is not sufficient to detect a controller that rebooted and is now
emitting events to a stale (or 0.0.0.0) target.
"""
from __future__ import annotations

from .const import TpiEventMode


def needs_event_reassertion(
    state: int,
    use_multicast: bool,
    unicast_target: tuple[str, int, int] | None,
    expected_ip: str | None,
    expected_port: int,
) -> tuple[bool, str]:
    """Decide whether the controller's TPI event config must be re-asserted.

    Args:
        state: raw QUERY_TPI_EVENT_EMIT_STATE mode byte.
        use_multicast: True if this controller is configured for multicast events.
        unicast_target: parsed (ip, port, mode) from
            QUERY_TPI_EVENT_UNICAST_ADDRESS, or None if that query failed or
            the controller gave no answer. Ignored in multicast mode.
        expected_ip: HA's IP as seen by the controller, or None if unknown.
        expected_port: the port HA listens on for unicast events.

    Returns:
        (needs_reassert, reason) — reason is a human-readable description
        for logging, empty when no re-assertion is needed.
    """
    if not (state & int(TpiEventMode.ENABLED)):
        return True, f"events not enabled (state=0x{state:02X})"

    if use_multicast:
        return False, ""

    if not (state & int(TpiEventMode.ENABLE_UNICAST_MODE)):
        # Not actually in unicast mode despite ENABLED being set — nothing
        # more we can check here; the ENABLE_UNICAST_MODE bit itself is
        # re-asserted on setup, this just avoids a spurious address check.
        return False, ""

    if unicast_target is None:
        return (
            True,
            f"no response to unicast address query, expected "
            f"{expected_ip}:{expected_port}",
        )

    stored_ip, stored_port, _mode = unicast_target
    if (
        stored_ip == "0.0.0.0"
        or (expected_ip is not None and stored_ip != expected_ip)
        or stored_port != expected_port
    ):
        return (
            True,
            f"event target is {stored_ip}:{stored_port}, "
            f"expected {expected_ip}:{expected_port}",
        )

    return False, ""
