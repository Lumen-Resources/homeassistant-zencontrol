"""Constants and enumerations for the zencontrol TPI Advanced protocol."""
from __future__ import annotations

from enum import IntEnum, IntFlag

# All TPI Advanced request frames start with this control byte
CONTROL_BYTE = 0x04

# Event frame header (ASCII "ZC")
EVENT_HEADER = bytes([0x5A, 0x43])

# Network
TPI_PORT = 5108
MULTICAST_ADDR = "239.255.90.67"
MULTICAST_PORT = 6969

# DALI addressing
DALI_BROADCAST = 0xFF
DALI_GROUP_OFFSET = 64       # Group 0 = address 64, Group 15 = address 79
DALI_CD_OFFSET = 64          # Control Device offset for EAN/serial queries
DALI_MAX_SHORT_ADDRESS = 63
DALI_MAX_GROUP = 15

# Special arc levels
ARC_LEVEL_MIXED = 0xFF       # Returned for groups with mixed levels
ARC_LEVEL_MAX = 254
ARC_LEVEL_MIN = 1
ARC_LEVEL_OFF = 0

# Profile
PROFILE_SCHEDULE = 0xFFFF    # Request schedule-determined profile


def group_to_address(group: int) -> int:
    """Convert group number 0-15 to DALI group address 64-79."""
    return group + DALI_GROUP_OFFSET


def address_to_group(address: int) -> int:
    """Convert DALI group address 64-79 to group number 0-15."""
    return address - DALI_GROUP_OFFSET


def is_group_address(address: int) -> bool:
    """Return True if the address is a DALI group address (64-79)."""
    return DALI_GROUP_OFFSET <= address <= DALI_GROUP_OFFSET + DALI_MAX_GROUP


# ---------------------------------------------------------------------------
# Command codes
# ---------------------------------------------------------------------------

class Command(IntEnum):
    QUERY_GROUP_LABEL = 0x01
    QUERY_SCENE_LABEL = 0x02                         # Legacy — use QUERY_SCENE_LABEL_FOR_GROUP
    QUERY_DALI_DEVICE_LABEL = 0x03
    QUERY_PROFILE_LABEL = 0x04
    QUERY_CURRENT_PROFILE_NUMBER = 0x05
    TRIGGER_SDDP_IDENTIFY = 0x06
    QUERY_TPI_EVENT_EMIT_STATE = 0x07
    ENABLE_TPI_EVENT_EMIT = 0x08
    QUERY_GROUP_NUMBERS = 0x09
    QUERY_SCENE_NUMBERS = 0x0A                       # Legacy
    QUERY_PROFILE_NUMBERS = 0x0B
    QUERY_OCCUPANCY_INSTANCE_TIMERS = 0x0C
    QUERY_INSTANCES_BY_ADDRESS = 0x0D
    DALI_COLOUR = 0x0E
    DMX_COLOUR = 0x10
    QUERY_GROUP_BY_NUMBER = 0x12
    QUERY_SCENE_BY_NUMBER = 0x13
    QUERY_SCENE_NUMBERS_BY_ADDRESS = 0x14
    QUERY_GROUP_MEMBERSHIP_BY_ADDRESS = 0x15
    QUERY_DALI_ADDRESSES_WITH_INSTANCES = 0x16
    QUERY_DMX_DEVICE_NUMBERS = 0x17
    QUERY_DMX_DEVICE_BY_NUMBER = 0x18
    QUERY_DMX_LEVEL_BY_CHANNEL = 0x19
    QUERY_SCENE_NUMBERS_FOR_GROUP = 0x1A
    QUERY_SCENE_LABEL_FOR_GROUP = 0x1B
    QUERY_CONTROLLER_VERSION_NUMBER = 0x1C
    QUERY_CONTROL_GEAR_DALI_ADDRESSES = 0x1D
    QUERY_SCENE_LEVELS_BY_ADDRESS = 0x1E
    QUERY_DMX_DEVICE_LABEL_BY_NUMBER = 0x20
    QUERY_INSTANCE_GROUPS = 0x21
    QUERY_DALI_FITTING_NUMBER = 0x22
    QUERY_DALI_INSTANCE_FITTING_NUMBER = 0x23
    QUERY_CONTROLLER_LABEL = 0x24
    QUERY_CONTROLLER_FITTING_NUMBER = 0x25
    QUERY_IS_DALI_READY = 0x26
    QUERY_CONTROLLER_STARTUP_COMPLETE = 0x27
    QUERY_OPERATING_MODE_BY_ADDRESS = 0x28
    OVERRIDE_DALI_BUTTON_LED_STATE = 0x29
    QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE = 0x30
    DALI_ADD_TPI_EVENT_FILTER = 0x31
    QUERY_DALI_TPI_EVENT_FILTERS = 0x32
    DALI_CLEAR_TPI_EVENT_FILTERS = 0x33
    QUERY_DALI_COLOUR = 0x34
    QUERY_DALI_COLOUR_FEATURES = 0x35
    SET_SYSTEM_VARIABLE = 0x36
    QUERY_SYSTEM_VARIABLE = 0x37
    QUERY_DALI_COLOUR_TEMP_LIMITS = 0x38
    SET_TPI_EVENT_UNICAST_ADDRESS = 0x40
    QUERY_TPI_EVENT_UNICAST_ADDRESS = 0x41
    QUERY_SYSTEM_VARIABLE_NAME = 0x42
    QUERY_PROFILE_INFORMATION = 0x43
    QUERY_COLOUR_SCENE_MEMBERSHIP_BY_ADDR = 0x44
    QUERY_COLOUR_SCENE_0_7_DATA_FOR_ADDR = 0x45
    QUERY_COLOUR_SCENE_8_11_DATA_FOR_ADDR = 0x46
    DALI_INHIBIT = 0xA0
    DALI_SCENE = 0xA1
    DALI_ARC_LEVEL = 0xA2
    DALI_ON_STEP_UP = 0xA3
    DALI_STEP_DOWN_OFF = 0xA4
    DALI_UP = 0xA5
    DALI_DOWN = 0xA6
    DALI_RECALL_MAX = 0xA7
    DALI_RECALL_MIN = 0xA8
    DALI_OFF = 0xA9
    DALI_QUERY_LEVEL = 0xAA
    DALI_QUERY_CONTROL_GEAR_STATUS = 0xAB
    DALI_QUERY_CG_TYPE = 0xAC
    DALI_QUERY_LAST_SCENE = 0xAD
    DALI_QUERY_LAST_SCENE_IS_CURRENT = 0xAE
    DALI_QUERY_MIN_LEVEL = 0xAF
    DALI_QUERY_MAX_LEVEL = 0xB0
    DALI_QUERY_FADE_RUNNING = 0xB1
    DALI_ENABLE_DAPC_SEQ = 0xB2
    VIRTUAL_INSTANCE = 0xB3
    DALI_CUSTOM_FADE = 0xB4
    DALI_GO_TO_LAST_ACTIVE_LEVEL = 0xB5
    QUERY_VIRTUAL_INSTANCES = 0xB6
    QUERY_DALI_INSTANCE_LABEL = 0xB7
    QUERY_DALI_EAN = 0xB8
    QUERY_DALI_SERIAL = 0xB9
    CHANGE_PROFILE_NUMBER = 0xC0
    DALI_STOP_FADE = 0xC1


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

class ResponseType(IntEnum):
    OK = 0xA0
    ANSWER = 0xA1
    NO_ANSWER = 0xA2
    ERROR = 0xA3


# ---------------------------------------------------------------------------
# Error codes (returned in response data when type is ERROR)
# ---------------------------------------------------------------------------

class ErrorCode(IntEnum):
    CHECKSUM = 0x01
    SHORT_CIRCUIT = 0x02
    RECEIVE_ERROR = 0x03
    UNKNOWN_CMD = 0x04
    PAID_FEATURE = 0xB0
    INVALID_ARGS = 0xB1
    CMD_REFUSED = 0xB2
    QUEUE_FAILURE = 0xB3
    RESPONSE_UNAVAIL = 0xB4
    OTHER_DALI_ERROR = 0xB5
    MAX_LIMIT = 0xB6
    UNEXPECTED_RESULT = 0xB7
    UNKNOWN_TARGET = 0xB8


# ---------------------------------------------------------------------------
# TPI event types
# ---------------------------------------------------------------------------

class EventType(IntEnum):
    BUTTON_PRESS = 0x00
    BUTTON_HOLD = 0x01
    ABSOLUTE_INPUT = 0x02
    LEVEL_CHANGE = 0x03
    GROUP_LEVEL_CHANGE = 0x04
    SCENE_CHANGE = 0x05
    OCCUPANCY = 0x06
    SYSTEM_VARIABLE_CHANGED = 0x07
    COLOUR_CHANGED = 0x08
    PROFILE_CHANGED = 0x09
    GROUP_OCCUPANCY = 0x0A
    LEVEL_CHANGE_V2 = 0x0B


# ---------------------------------------------------------------------------
# TPI event mode flags
# ---------------------------------------------------------------------------

class TpiEventMode(IntFlag):
    DISABLED = 0x00
    ENABLED = 0x01
    DALI_EVENT_FILTERING = 0x02
    ENABLE_UNICAST_MODE = 0x40
    DISABLE_MULTICAST_MODE = 0x80


# ---------------------------------------------------------------------------
# Colour types (used in DALI_COLOUR command and QUERY_DALI_COLOUR response)
# ---------------------------------------------------------------------------

class ColourType(IntEnum):
    XY = 0x10
    TC = 0x20
    RGBWAF = 0x80


# ---------------------------------------------------------------------------
# DALI status bitmask (DALI_QUERY_CONTROL_GEAR_STATUS)
# ---------------------------------------------------------------------------

class DaliStatusMask(IntFlag):
    CG_FAILURE = 0x01
    LAMP_FAILURE = 0x02
    LAMP_POWER_ON = 0x04
    LIMIT_ERROR = 0x08
    FADE_RUNNING = 0x10
    RESET = 0x20
    MISSING_SHORT_ADDRESS = 0x40
    POWER_FAILURE = 0x80


# ---------------------------------------------------------------------------
# DALI control gear type bitmask (DALI_QUERY_CG_TYPE) — 32-bit little-endian
# ---------------------------------------------------------------------------

class DaliCgTypeMask(IntFlag):
    FLUORESCENT = 0x00000001
    EMERGENCY = 0x00000002
    DISCHARGE = 0x00000004
    HALOGEN = 0x00000008
    INCANDESCENT = 0x00000010
    DC = 0x00000020
    LED = 0x00000040
    RELAY = 0x00000080
    COLOUR_CONTROL = 0x00000100
    LOAD_REFERENCING = 0x00008000
    THERMAL_GEAR_PROTECTION = 0x00010000
    DIMMING_CURVE_SELECTION = 0x00020000


# ---------------------------------------------------------------------------
# Colour feature flags (QUERY_DALI_COLOUR_FEATURES response byte)
# Bit 0: XY capable
# Bit 1: Tc (tunable white) capable
# Bits 2-4: number of primaries
# Bits 5-7: number of RGBWAF channels
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DALI control device instance types
# ---------------------------------------------------------------------------

class InstanceType(IntEnum):
    PUSH_BUTTON = 0x01
    ABSOLUTE_INPUT = 0x02
    OCCUPANCY_SENSOR = 0x03
    LIGHT_SENSOR = 0x04
    GENERAL_PURPOSE = 0x06


# ---------------------------------------------------------------------------
# Instance binary states (used by OVERRIDE_DALI_BUTTON_LED_STATE and
# QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE). Note: logical low/high are not 0/1.
# ---------------------------------------------------------------------------

class InstanceBinaryState(IntEnum):
    UNKNOWN = 0x00
    LO = 0x01        # Logical low  → LED off
    HI = 0x02        # Logical high → LED on


def parse_colour_features(byte: int) -> dict:
    """Parse the colour features byte into a dict of capabilities."""
    return {
        "xy": bool(byte & 0x01),
        "tc": bool(byte & 0x02),
        "primaries": (byte >> 2) & 0x07,
        "rgbwaf_channels": (byte >> 5) & 0x07,
    }
