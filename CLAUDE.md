# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (`custom_components/zencontrol`) for zencontrol lighting controllers using the **TPI Advanced** protocol over UDP/TCP. The TPI Advanced spec is at `C:\Users\Darren\Downloads\Advanced_Third_Party_Interface_API_Document_20_11_2025.pdf.pdf`.

## Development commands

There is no build step. Syntax-check all Python files with:
```bash
python -c "
import ast, glob
for f in glob.glob('custom_components/**/*.py', recursive=True):
    ast.parse(open(f).read())
    print('OK', f)
"
```

To install into a local HA dev environment, symlink or copy `custom_components/zencontrol/` into the HA `config/custom_components/` directory and restart HA.

## Architecture

### Layer separation

```
custom_components/zencontrol/
├── tpi/                  ← Pure protocol library — no HA dependency
│   ├── const.py          ← All enums (Command, EventType, ColourType, DaliCgTypeMask, …)
│   ├── protocol.py       ← Frame builders, parsers, checksum (XOR of all bytes)
│   ├── client.py         ← Async UDP/TCP transport; seq-counter → asyncio.Future mapping
│   ├── commands.py       ← Typed command wrappers; returns dataclasses not raw bytes
│   └── event_listener.py ← Shared UDP socket; routes events by source IP
├── coordinator.py        ← Per-controller DataUpdateCoordinator
├── config_flow.py        ← ConfigFlow + OptionsFlow
├── __init__.py           ← async_setup_entry / async_unload_entry
├── device_trigger.py     ← Device triggers for push buttons (press/hold)
└── light/scene/select/switch/binary_sensor/event.py  ← HA entity platforms
```

### TPI protocol key facts

- All TPI Advanced request frames start with control byte `0x04`.
- Checksum = XOR of all preceding bytes; verify by XOR-ing all bytes including checksum (result must be 0).
- Sequence counter (byte 2) is 0–255 wrapping; used to match responses to requests.
- **Basic frame** (8 bytes): `[0x04, seq, cmd, address, data_hi, data_mid, data_lo, checksum]`
- **Dynamic frame**: `[0x04, seq, cmd, data_len, ...data, checksum]`
- **DALI Colour frame**: `[0x04, seq, 0x0E, address, arc_level, colour_type, 7-byte colour data, checksum]`
- Response frame: `[response_type, seq, data_len, ...data, checksum]` — `0xA0`=OK, `0xA1`=ANSWER, `0xA2`=NO_ANSWER, `0xA3`=ERROR
- Event frames start with `0x5A 0x43` ("ZC") and are sent to multicast `239.255.90.67:6969` or unicast.

### DALI addressing (TPI-specific, not raw DALI)

| Target | Address byte |
|---|---|
| Short address 0–63 | 0–63 |
| Group 0–15 | 64–79 (group + 64) |
| Broadcast | 0xFF |

Exception: commands that only operate on groups use 0–15 directly (e.g. `QUERY_GROUP_LABEL`, `QUERY_SCENE_NUMBERS_FOR_GROUP`).

### Coordinator / state flow

1. `async_config_entry_first_refresh()` triggers `_async_update_data()` → `_discover()`.
2. Discovery queries groups, scenes per group, profiles, and short address metadata sequentially.
3. After discovery, `setup_events()` registers the coordinator with the shared `EventListener` and sends `SET_TPI_EVENT_UNICAST_ADDRESS` + `ENABLE_TPI_EVENT_EMIT` to the controller.
4. Push events (`LEVEL_CHANGE_EVENT_V2`, `COLOUR_CHANGED_EVENT`, `SCENE_CHANGE_EVENT`, `PROFILE_CHANGED_EVENT`) call `async_set_updated_data()` — no polling needed for live state.
5. Every 30 s, `_async_update_data()` calls `_check_and_assert_events()` which re-asserts unicast config if the controller has rebooted (detects via `QUERY_TPI_EVENT_EMIT_STATE`).

### Shared event listener

One `EventListener` UDP socket is shared across all config entries (controllers) within a single HA instance. It lives at `hass.data[DOMAIN][DATA_EVENT_LISTENER]` and is started by the first entry, stopped when the last entry is removed. Events are dispatched to the correct coordinator by matching the UDP source IP to `coordinator._host`.

### Entity → address mapping

- `ZenGroupLight` / `ZenScene` target DALI group address (group_number + 64).
- `ZenShortAddressLight` / `ZenRelaySwitch` target the raw short address (0–63).
- Relay detection: `DALI_HW_RELAY` flag in `DALI_QUERY_CG_TYPE` response → `switch.py` instead of `light.py`.
- Colour mode for short addresses is resolved from `QUERY_DALI_COLOUR_FEATURES` at discovery time.

### Control-device instances (occupancy, buttons, absolute inputs)

- `coordinator._discover_instances()` does a single walk over control devices (`QUERY_DALI_ADDRESSES_WITH_INSTANCES` → `QUERY_INSTANCES_BY_ADDRESS`) and dispatches each instance by `InstanceType`: occupancy → `binary_sensor.py`, push button → `event.py` (+ `switch.py` LED), absolute input → `binary_sensor.py` (on/off, read-only).
- All instance addresses are DALI **CD addresses (64–127)**; the event `target` is already in this range (no +64 needed, unlike group addresses).
- **Buttons** are transient: `_handle_button` fires the `SIGNAL_BUTTON_EVENT` dispatcher signal `(cd, instance, event_type)`. Both the `event` entity and `device_trigger.py` subscribe to this single signal — there is no coordinator-data state for buttons.
- **Button LEDs** (`OVERRIDE_DALI_BUTTON_LED_STATE` / `QUERY_LAST_KNOWN_DALI_BUTTON_LED_STATE`): no "has LED" query exists, so LED switches are created for every button but `entity_registry_enabled_default` is set to whether a definite state was read at discovery. State is optimistic.
- **Absolute inputs** are stateful, read-only on/off: `_handle_absolute_input` stores the raw 16-bit value and calls `async_set_updated_data`; the `binary_sensor` is `on` when the value is non-zero. They emit only on a value *change* (turning a dial), not on a press.
- **Gotcha — events require an active profile:** the controller only forwards an instance's TPI events when that instance is active in the *running profile*. An inactive instance stays silent on the TPI feed even though it appears in discovery and in the controller's own event log. If an entity never updates but no TPI event arrives, check the controller profile before suspecting this code.

### Colour handling

- `ColourType.TC` (0x20) → `ColorMode.COLOR_TEMP`; limits from `QUERY_DALI_COLOUR_TEMP_LIMITS`.
- `ColourType.RGBWAF` (0x80) → `ColorMode.RGBW` (≥4 channels) or `ColorMode.RGB`.
- `ColourType.XY` (0x10) → `ColorMode.XY`; HA uses 0.0–1.0 floats, TPI uses 0–0xFFFE integers.
- `arc_level=0xFF` in a colour frame means "change colour only, no arc change".
- Kelvin→Mirek rounding: TPI accepts Kelvin but DALI hardware uses Mirek (1,000,000 / K). A round-trip query may return a slightly different Kelvin due to integer rounding.

### Config entry data keys (`const.py`)

`CONF_HOST`, `CONF_PORT` (default 5108), `CONF_EVENT_PORT` (default 6970), `CONF_USE_MULTICAST` (default False), `CONF_SHORT_ADDRESSES` (list of ints).
