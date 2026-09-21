"""Tests for the coordinator's TPI event re-assertion decision logic.

This exercises tpi.event_check.needs_event_reassertion(), the pure function
extracted from ZenControlCoordinator._check_and_assert_events() so the
"controller rebooted and silently lost its unicast target" scenario can be
unit tested without a Home Assistant install (coordinator.py itself imports
homeassistant, which is not available in this test environment).
"""
from tpi.event_check import needs_event_reassertion

ENABLED = 0x01
ENABLE_UNICAST_MODE = 0x40
UNICAST_ENABLED_STATE = ENABLED | ENABLE_UNICAST_MODE  # 0x41, matches the bug report


def test_disabled_state_always_needs_reassertion():
    needs, reason = needs_event_reassertion(
        state=0x00,
        use_multicast=False,
        unicast_target=None,
        expected_ip="10.15.0.60",
        expected_port=6970,
    )
    assert needs is True
    assert "not enabled" in reason


def test_multicast_mode_skips_address_check():
    # Multicast has no per-controller unicast target to verify.
    needs, reason = needs_event_reassertion(
        state=ENABLED,
        use_multicast=True,
        unicast_target=None,
        expected_ip=None,
        expected_port=6970,
    )
    assert needs is False
    assert reason == ""


def test_matching_unicast_target_does_not_reassert():
    needs, reason = needs_event_reassertion(
        state=UNICAST_ENABLED_STATE,
        use_multicast=False,
        unicast_target=("10.15.0.60", 6970, UNICAST_ENABLED_STATE),
        expected_ip="10.15.0.60",
        expected_port=6970,
    )
    assert needs is False
    assert reason == ""


def test_lost_unicast_address_after_reboot_triggers_reassertion():
    # The exact live-hardware failure mode: mode byte retained (0x41), IP lost.
    needs, reason = needs_event_reassertion(
        state=UNICAST_ENABLED_STATE,
        use_multicast=False,
        unicast_target=("0.0.0.0", 6970, UNICAST_ENABLED_STATE),
        expected_ip="10.15.0.60",
        expected_port=6970,
    )
    assert needs is True
    assert "0.0.0.0" in reason
    assert "10.15.0.60:6970" in reason


def test_stale_ip_triggers_reassertion():
    needs, reason = needs_event_reassertion(
        state=UNICAST_ENABLED_STATE,
        use_multicast=False,
        unicast_target=("192.168.1.5", 6970, UNICAST_ENABLED_STATE),
        expected_ip="10.15.0.60",
        expected_port=6970,
    )
    assert needs is True
    assert "192.168.1.5:6970" in reason


def test_wrong_port_triggers_reassertion():
    needs, reason = needs_event_reassertion(
        state=UNICAST_ENABLED_STATE,
        use_multicast=False,
        unicast_target=("10.15.0.60", 1234, UNICAST_ENABLED_STATE),
        expected_ip="10.15.0.60",
        expected_port=6970,
    )
    assert needs is True


def test_no_answer_to_address_query_triggers_reassertion():
    needs, reason = needs_event_reassertion(
        state=UNICAST_ENABLED_STATE,
        use_multicast=False,
        unicast_target=None,
        expected_ip="10.15.0.60",
        expected_port=6970,
    )
    assert needs is True
    assert "no response" in reason


def test_unknown_ha_ip_does_not_falsely_flag_mismatch():
    # If we couldn't resolve our own IP, don't compare against it — only the
    # 0.0.0.0 / port checks still apply.
    needs, reason = needs_event_reassertion(
        state=UNICAST_ENABLED_STATE,
        use_multicast=False,
        unicast_target=("10.15.0.60", 6970, UNICAST_ENABLED_STATE),
        expected_ip=None,
        expected_port=6970,
    )
    assert needs is False
