"""Command-wrapper tests using a fake transport client.

Exercises the request framing and response decoding of ZenCommands without a
controller or Home Assistant.
"""
import asyncio

from tpi.commands import ZenCommands
from tpi.const import Command, InstanceType, ResponseType
from tpi.protocol import Response


class FakeClient:
    """Stands in for TpiClient: records frames, returns canned responses."""

    def __init__(self, responses: dict) -> None:
        # responses: {command_byte: Response | Exception | callable(frame) -> Response}
        self._responses = responses
        self.sent: list[bytes] = []
        self._seq = 0

    def next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return seq

    async def send(self, frame: bytes, seq: int) -> Response:
        self.sent.append(frame)
        resp = self._responses[frame[2]]
        if callable(resp):
            resp = resp(frame)
        if isinstance(resp, Exception):
            raise resp
        return resp


def answer(data: bytes) -> Response:
    return Response(response_type=ResponseType.ANSWER, seq=0, data=data)


OK = Response(response_type=ResponseType.OK, seq=0)
NO_ANSWER = Response(response_type=ResponseType.NO_ANSWER, seq=0)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Startup-complete semantics (regression for the always-False bug)
# ---------------------------------------------------------------------------

def test_startup_complete_ok_means_ready():
    cmds = ZenCommands(FakeClient({Command.QUERY_CONTROLLER_STARTUP_COMPLETE: OK}))
    assert run(cmds.query_startup_complete()) is True


def test_startup_complete_no_answer_means_not_ready():
    cmds = ZenCommands(FakeClient({Command.QUERY_CONTROLLER_STARTUP_COMPLETE: NO_ANSWER}))
    assert run(cmds.query_startup_complete()) is False


def test_startup_complete_timeout_means_not_ready():
    cmds = ZenCommands(
        FakeClient({Command.QUERY_CONTROLLER_STARTUP_COMPLETE: asyncio.TimeoutError()})
    )
    assert run(cmds.query_startup_complete()) is False


def test_startup_complete_data_byte_fallback():
    cmds = ZenCommands(
        FakeClient({Command.QUERY_CONTROLLER_STARTUP_COMPLETE: answer(bytes([1]))})
    )
    assert run(cmds.query_startup_complete()) is True


# ---------------------------------------------------------------------------
# Bitmask decoding
# ---------------------------------------------------------------------------

def test_query_group_membership_decodes_both_bytes():
    # data[0] = groups 8-15, data[1] = groups 0-7 (per spec)
    cmds = ZenCommands(
        FakeClient({Command.QUERY_GROUP_MEMBERSHIP_BY_ADDRESS: answer(bytes([0x80, 0x01]))})
    )
    assert run(cmds.query_group_membership(5)) == {0, 15}


def test_query_group_membership_no_answer_is_empty():
    cmds = ZenCommands(FakeClient({Command.QUERY_GROUP_MEMBERSHIP_BY_ADDRESS: NO_ANSWER}))
    assert run(cmds.query_group_membership(5)) == set()


def test_query_control_gear_addresses_bitmask():
    mask = bytes([0b00000011, 0, 0, 0, 0, 0, 0, 0b10000000])
    cmds = ZenCommands(
        FakeClient({Command.QUERY_CONTROL_GEAR_DALI_ADDRESSES: answer(mask)})
    )
    assert run(cmds.query_control_gear_addresses()) == [0, 1, 63]


# ---------------------------------------------------------------------------
# Instance queries
# ---------------------------------------------------------------------------

def test_query_instances_by_address_parses_records():
    # Two 4-byte records: (inst 0, PUSH_BUTTON), (inst 1, ABSOLUTE_INPUT)
    data = bytes([0, 0x01, 0, 0, 1, 0x02, 0, 0])
    cmds = ZenCommands(FakeClient({Command.QUERY_INSTANCES_BY_ADDRESS: answer(data)}))
    instances = run(cmds.query_instances_by_address(65))
    assert [(i.instance_number, i.instance_type) for i in instances] == [
        (0, InstanceType.PUSH_BUTTON),
        (1, InstanceType.ABSOLUTE_INPUT),
    ]


def test_query_occupancy_timer_parses_and_defaults():
    # [deadtime, hold, report, last_detect_hi, last_detect_lo]
    cmds = ZenCommands(
        FakeClient({Command.QUERY_OCCUPANCY_INSTANCE_TIMERS: answer(bytes([5, 90, 10, 0x01, 0x2C]))})
    )
    timer = run(cmds.query_occupancy_timer(68, 0))
    assert timer.hold_time_s == 90
    assert timer.last_detect_s == 300

    # hold time of 0 falls back to 60 s
    cmds = ZenCommands(
        FakeClient({Command.QUERY_OCCUPANCY_INSTANCE_TIMERS: answer(bytes([5, 0, 10, 0, 1]))})
    )
    assert run(cmds.query_occupancy_timer(68, 0)).hold_time_s == 60


# ---------------------------------------------------------------------------
# Button LEDs
# ---------------------------------------------------------------------------

def test_override_button_led_matches_spec_frame():
    client = FakeClient({Command.OVERRIDE_DALI_BUTTON_LED_STATE: OK})
    cmds = ZenCommands(client)
    assert run(cmds.override_button_led(0x70, 1, on=True)) is True
    # Spec worked example: 04 00 29 70 00 02 01 5E
    assert client.sent[0] == bytes.fromhex("040029700002015E")


def test_query_button_led_state_tristate():
    for data_byte, expected in ((0x02, True), (0x01, False), (0x00, None)):
        cmds = ZenCommands(
            FakeClient(
                {Command.QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE: answer(bytes([data_byte]))}
            )
        )
        assert run(cmds.query_button_led_state(0x70, 1)) is expected


# ---------------------------------------------------------------------------
# System variables
# ---------------------------------------------------------------------------

def test_query_system_variable_name():
    cmds = ZenCommands(FakeClient({Command.QUERY_SYSTEM_VARIABLE_NAME: answer(b"Dog")}))
    assert run(cmds.query_system_variable_name(16)) == "Dog"

    cmds = ZenCommands(FakeClient({Command.QUERY_SYSTEM_VARIABLE_NAME: NO_ANSWER}))
    assert run(cmds.query_system_variable_name(16)) is None

    # whitespace-only label is treated as unnamed
    cmds = ZenCommands(FakeClient({Command.QUERY_SYSTEM_VARIABLE_NAME: answer(b"  ")}))
    assert run(cmds.query_system_variable_name(16)) is None
