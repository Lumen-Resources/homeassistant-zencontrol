"""Frame construction and parsing tests.

Byte-level golden vectors are taken directly from the worked examples in the
TPI Advanced API document (20/11/2025), several of which were also verified
against live controllers during development.
"""
import pytest

from tpi.const import (
    ColourType,
    EventType,
    ResponseType,
    address_to_group,
    group_to_address,
    is_group_address,
    parse_colour_features,
)
from tpi.commands import parse_colour_payload
from tpi.protocol import (
    build_basic_frame,
    build_dali_colour_frame,
    build_dynamic_frame,
    build_rgbwaf_colour_data,
    build_tc_colour_data,
    build_unicast_address_frame,
    build_xy_colour_data,
    calc_checksum,
    parse_event,
    parse_response,
    verify_checksum,
)


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------

def test_checksum_roundtrip():
    body = bytes([0x04, 0x00, 0x27, 0x00, 0x00, 0x00, 0x00])
    assert calc_checksum(body) == 0x23
    assert verify_checksum(body + bytes([0x23]))
    assert not verify_checksum(body + bytes([0x24]))


# ---------------------------------------------------------------------------
# Basic frame builder — golden vectors from the spec
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # QUERY_CONTROLLER_STARTUP_COMPLETE
        (dict(seq=0, cmd=0x27), bytes.fromhex("04002700000000 23".replace(" ", ""))),
        # SET_SYSTEM_VARIABLE 3 = 0xFFFE
        (
            dict(seq=0, cmd=0x36, address=0x03, data_mid=0xFF, data_lo=0xFE),
            bytes.fromhex("0400360300FFFE30"),
        ),
        # QUERY_GROUP_MEMBERSHIP_BY_ADDRESS for address 15
        (dict(seq=0, cmd=0x15, address=0x0F), bytes.fromhex("0400150F0000001E")),
        # OVERRIDE_DALI_BUTTON_LED_STATE addr 112 inst 1 -> On (HI=0x02)
        (
            dict(seq=0, cmd=0x29, address=0x70, data_mid=0x02, data_lo=0x01),
            bytes.fromhex("040029700002015E"),
        ),
        # QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE addr 112 inst 1
        (
            dict(seq=0, cmd=0x30, address=0x70, data_lo=0x01),
            bytes.fromhex("0400307000000145"),
        ),
    ],
)
def test_build_basic_frame_spec_vectors(kwargs, expected):
    assert build_basic_frame(**kwargs) == expected


def test_build_unicast_address_frame_spec_vector():
    # SET_TPI_EVENT_UNICAST_ADDRESS -> 192.168.10.10:8811
    frame = build_unicast_address_frame(0, "192.168.10.10", 8811)
    assert frame == bytes.fromhex("040040 06 226B C0A80A0A 63".replace(" ", ""))


def test_build_dynamic_frame_structure():
    frame = build_dynamic_frame(5, 0x31, bytes([0xAA, 0xBB]))
    assert frame[0] == 0x04
    assert frame[1] == 5
    assert frame[2] == 0x31
    assert frame[3] == 2                      # data length
    assert frame[4:6] == bytes([0xAA, 0xBB])
    assert verify_checksum(frame)


def test_build_dali_colour_frame_pads_to_seven_bytes():
    frame = build_dali_colour_frame(0, 10, 0xFF, ColourType.TC, bytes([0x0A, 0x28]))
    assert len(frame) == 14                   # 6 header + 7 colour + checksum
    assert frame[2] == 0x0E                   # DALI_COLOUR command
    assert frame[4] == 0xFF                   # arc level: colour-only
    assert frame[5] == ColourType.TC
    assert frame[6:13] == bytes([0x0A, 0x28, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    assert verify_checksum(frame)


def test_colour_data_builders():
    assert build_tc_colour_data(2700) == bytes([0x0A, 0x8C, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    assert build_rgbwaf_colour_data(1, 2, 3) == bytes([1, 2, 3, 0xFF, 0xFF, 0xFF, 0xFF])
    assert build_xy_colour_data(0xFFFE, 0x0001) == bytes(
        [0xFF, 0xFE, 0x00, 0x01, 0xFF, 0xFF, 0xFF]
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_parse_response_answer_spec_vector():
    # QUERY_SYSTEM_VARIABLE response: value 0xFFFE.
    # Note: the spec's worked example shows checksum 0x5C, which does not
    # XOR-validate — a documentation typo. Real controllers emit XOR-valid
    # frames (verified against live hardware); the correct checksum is 0xA2.
    resp = parse_response(bytes.fromhex("A10002FFFEA2"))
    assert resp is not None
    assert resp.response_type == ResponseType.ANSWER
    assert resp.seq == 0
    assert resp.data == bytes([0xFF, 0xFE])
    assert resp.has_data and resp.ok and not resp.is_error


def test_parse_response_ok_no_data():
    resp = parse_response(bytes.fromhex("A00000A0"))
    assert resp is not None
    assert resp.response_type == ResponseType.OK
    assert resp.ok and not resp.has_data and not resp.no_answer


def test_parse_response_rejects_garbage():
    assert parse_response(b"") is None
    assert parse_response(bytes.fromhex("A100")) is None            # too short
    assert parse_response(bytes.fromhex("A10002FFFEA3")) is None    # bad checksum
    assert parse_response(bytes.fromhex("550000 55".replace(" ", ""))) is None  # unknown type


# ---------------------------------------------------------------------------
# Event parsing — golden vectors from the spec
# ---------------------------------------------------------------------------

MAC = "7CBACC2F402E"


def test_parse_event_button_press():
    raw = bytes.fromhex(f"5A43{MAC}007B0001052D")
    event = parse_event(raw)
    assert event is not None
    assert event.event_type == EventType.BUTTON_PRESS
    assert event.target == 123                # CD address (59 + 64)
    assert event.data == bytes([0x05])        # instance number


def test_parse_event_absolute_input():
    raw = bytes.fromhex(f"5A43{MAC}007B020305AABB3C")
    event = parse_event(raw)
    assert event is not None
    assert event.event_type == EventType.ABSOLUTE_INPUT
    assert event.target == 123
    instance, hi, lo = event.data
    assert instance == 5
    assert (hi << 8) | lo == 0xAABB


def test_parse_event_system_variable_changed():
    # Sysvar 32 changed to -200 with magnitude -1 (actual value -20.0)
    raw = bytes.fromhex(f"5A43{MAC}00200705FFFFFF38FF48")
    event = parse_event(raw)
    assert event is not None
    assert event.event_type == EventType.SYSTEM_VARIABLE_CHANGED
    assert event.target == 32
    raw_value = int.from_bytes(event.data[0:4], "big", signed=True)
    magnitude = int.from_bytes(event.data[4:5], "big", signed=True)
    assert raw_value == -200
    assert magnitude == -1
    assert raw_value * (10 ** magnitude) == pytest.approx(-20.0)


def test_parse_event_rejects_garbage():
    good = bytes.fromhex(f"5A43{MAC}007B0001052D")
    assert parse_event(good[:-2]) is None                       # too short
    assert parse_event(b"XX" + good[2:]) is None                # bad header
    assert parse_event(good[:-1] + bytes([0x00])) is None       # bad checksum


# ---------------------------------------------------------------------------
# Addressing helpers & feature/colour payload parsing
# ---------------------------------------------------------------------------

def test_group_addressing_helpers():
    assert group_to_address(0) == 64
    assert group_to_address(15) == 79
    assert address_to_group(79) == 15
    assert is_group_address(64) and is_group_address(79)
    assert not is_group_address(63) and not is_group_address(80)


def test_parse_colour_features():
    # tc=1, primaries=3, rgbwaf_channels=4  ->  0x02 | (3<<2) | (4<<5)
    parsed = parse_colour_features(0x02 | (3 << 2) | (4 << 5))
    assert parsed == {"xy": False, "tc": True, "primaries": 3, "rgbwaf_channels": 4}


def test_parse_colour_payload_tc():
    state = parse_colour_payload(ColourType.TC, bytes([0x0A, 0x8C]))
    assert state.colour_type == ColourType.TC
    assert state.kelvin == 2700


def test_parse_colour_payload_rgbw():
    state = parse_colour_payload(ColourType.RGBWAF, bytes([10, 20, 30, 40]))
    assert (state.r, state.g, state.b, state.w) == (10, 20, 30, 40)
    assert state.a is None

    rgb_only = parse_colour_payload(ColourType.RGBWAF, bytes([1, 2, 3]))
    assert (rgb_only.r, rgb_only.g, rgb_only.b) == (1, 2, 3)
    assert rgb_only.w is None


def test_parse_colour_payload_xy():
    state = parse_colour_payload(ColourType.XY, bytes([0xFF, 0xFE, 0x00, 0x01]))
    assert state.x == 0xFFFE
    assert state.y == 0x0001
