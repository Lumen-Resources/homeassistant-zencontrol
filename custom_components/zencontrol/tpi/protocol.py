"""TPI Advanced frame construction and parsing."""
from __future__ import annotations

from dataclasses import dataclass, field

from .const import (
    CONTROL_BYTE,
    EVENT_HEADER,
    Command,
    ColourType,
    EventType,
    ResponseType,
)


# ---------------------------------------------------------------------------
# Checksum helpers
# ---------------------------------------------------------------------------

def calc_checksum(data: bytes) -> int:
    """XOR all bytes together to produce the checksum byte."""
    result = 0
    for b in data:
        result ^= b
    return result


def verify_checksum(data: bytes) -> bool:
    """Return True if XOR of all bytes (including checksum) equals zero."""
    result = 0
    for b in data:
        result ^= b
    return result == 0


# ---------------------------------------------------------------------------
# Request frame builders
# ---------------------------------------------------------------------------

def build_basic_frame(
    seq: int,
    cmd: int,
    address: int = 0x00,
    data_hi: int = 0x00,
    data_mid: int = 0x00,
    data_lo: int = 0x00,
) -> bytes:
    """Build an 8-byte Basic Request Frame.

    Structure: Control | Seq | Cmd | Address | DataHi | DataMid | DataLo | Checksum
    """
    body = bytes([CONTROL_BYTE, seq & 0xFF, cmd & 0xFF, address & 0xFF,
                  data_hi & 0xFF, data_mid & 0xFF, data_lo & 0xFF])
    return body + bytes([calc_checksum(body)])


def build_dynamic_frame(seq: int, cmd: int, data: bytes) -> bytes:
    """Build a TPI Dynamic Subframe (variable-length data payload).

    Structure: Control | Seq | Cmd | DataLength | Data... | Checksum
    """
    header = bytes([CONTROL_BYTE, seq & 0xFF, cmd & 0xFF, len(data) & 0xFF])
    body = header + data
    return body + bytes([calc_checksum(body)])


def build_dali_colour_frame(
    seq: int,
    address: int,
    arc_level: int,
    colour_type: ColourType,
    colour_data: bytes,
) -> bytes:
    """Build a DALI Colour Request Frame.

    colour_data must be exactly 7 bytes; unused trailing bytes should be 0xFF.
    arc_level 0xFF means colour-only fade (no arc change).
    """
    # Pad or truncate colour_data to exactly 7 bytes
    padded = (colour_data + bytes([0xFF] * 7))[:7]
    body = bytes([
        CONTROL_BYTE,
        seq & 0xFF,
        Command.DALI_COLOUR,
        address & 0xFF,
        arc_level & 0xFF,
        colour_type & 0xFF,
    ]) + padded
    return body + bytes([calc_checksum(body)])


def build_unicast_address_frame(seq: int, ip: str, port: int) -> bytes:
    """Build a SET_TPI_EVENT_UNICAST_ADDRESS Dynamic frame.

    Data: [port_hi, port_lo, ip_byte0, ip_byte1, ip_byte2, ip_byte3]
    """
    ip_parts = [int(x) for x in ip.split(".")]
    data = bytes([
        (port >> 8) & 0xFF,
        port & 0xFF,
        ip_parts[0],
        ip_parts[1],
        ip_parts[2],
        ip_parts[3],
    ])
    return build_dynamic_frame(seq, Command.SET_TPI_EVENT_UNICAST_ADDRESS, data)


def parse_unicast_address(data: bytes) -> tuple[str, int, int] | None:
    """Parse the QUERY_TPI_EVENT_UNICAST_ADDRESS answer payload.

    Data: [mode, port_lo, port_hi, ip_byte0, ip_byte1, ip_byte2, ip_byte3]

    Note the port here is little-endian and prefixed with a mode byte —
    this is *not* the same layout as build_unicast_address_frame(), which
    sends a big-endian port with no mode byte. Returns None if data is
    shorter than 7 bytes.
    """
    if len(data) < 7:
        return None
    mode = data[0]
    port = data[1] | (data[2] << 8)
    ip = ".".join(str(b) for b in data[3:7])
    return (ip, port, mode)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

@dataclass
class Response:
    """Parsed TPI Advanced response frame."""
    response_type: ResponseType
    seq: int
    data: bytes = field(default_factory=bytes)

    @property
    def ok(self) -> bool:
        """True for OK or ANSWER responses."""
        return self.response_type in (ResponseType.OK, ResponseType.ANSWER)

    @property
    def has_data(self) -> bool:
        return self.response_type == ResponseType.ANSWER and len(self.data) > 0

    @property
    def no_answer(self) -> bool:
        return self.response_type == ResponseType.NO_ANSWER

    @property
    def is_error(self) -> bool:
        return self.response_type == ResponseType.ERROR


def parse_response(raw: bytes) -> Response | None:
    """Parse a raw TPI Advanced response frame.

    Minimum valid frame: [ResponseType, Seq, DataLen, Checksum] = 4 bytes.
    Returns None if the frame is too short or the checksum is invalid.
    """
    if len(raw) < 4:
        return None
    if not verify_checksum(raw):
        return None
    try:
        resp_type = ResponseType(raw[0])
    except ValueError:
        return None
    seq = raw[1]
    data_len = raw[2]
    payload = bytes(raw[3: 3 + data_len]) if data_len > 0 else b""
    return Response(response_type=resp_type, seq=seq, data=payload)


# ---------------------------------------------------------------------------
# Event frame parsing
# ---------------------------------------------------------------------------

@dataclass
class TpiEvent:
    """Parsed TPI Event Multicast/Unicast frame."""
    mac: bytes          # 6 bytes — MAC address of the sending controller
    target: int         # 2-byte target (address, group+64, system var index, …)
    event_type: EventType
    data: bytes = field(default_factory=bytes)


def parse_event(raw: bytes) -> TpiEvent | None:
    """Parse a raw TPI Event frame.

    Minimum frame: ZC(2) + MAC(6) + Target(2) + EventType(1) + DataLen(1) + Checksum(1) = 13 bytes
    Returns None if the header magic is wrong, frame too short, or checksum invalid.
    """
    if len(raw) < 13:
        return None
    if raw[0] != EVENT_HEADER[0] or raw[1] != EVENT_HEADER[1]:
        return None
    if not verify_checksum(raw):
        return None
    mac = bytes(raw[2:8])
    target = (raw[8] << 8) | raw[9]
    try:
        event_type = EventType(raw[10])
    except ValueError:
        return None
    data_len = raw[11]
    payload = bytes(raw[12: 12 + data_len]) if data_len > 0 else b""
    return TpiEvent(mac=mac, target=target, event_type=event_type, data=payload)


# ---------------------------------------------------------------------------
# Colour data helpers
# ---------------------------------------------------------------------------

def build_tc_colour_data(kelvin: int) -> bytes:
    """Pack a Tc colour value (Kelvin) into 7 bytes for a DALI colour frame."""
    hi = (kelvin >> 8) & 0xFF
    lo = kelvin & 0xFF
    return bytes([hi, lo, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])


def build_rgbwaf_colour_data(
    r: int, g: int, b: int,
    w: int = 0xFF, a: int = 0xFF, f: int = 0xFF,
) -> bytes:
    """Pack RGBWAF colour values (0–254, 0xFF = no change) into 7 bytes.

    The 7th byte is the DALI RGB control byte; set to 0xFF (unused at TPI level).
    """
    return bytes([r & 0xFF, g & 0xFF, b & 0xFF,
                  w & 0xFF, a & 0xFF, f & 0xFF, 0xFF])


def build_xy_colour_data(x: int, y: int) -> bytes:
    """Pack CIE 1931 XY colour values (0–0xFFFE, 0xFFFF = no change) into 7 bytes."""
    return bytes([
        (x >> 8) & 0xFF, x & 0xFF,
        (y >> 8) & 0xFF, y & 0xFF,
        0xFF, 0xFF, 0xFF,
    ])


