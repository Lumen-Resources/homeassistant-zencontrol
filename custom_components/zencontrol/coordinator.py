"""zencontrol coordinator — per-controller discovery, state management, event routing."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BUTTON_EVENT_HOLD,
    BUTTON_EVENT_PRESS,
    CONF_EVENT_PORT,
    CONF_HOST,
    CONF_LOAD_ADDRESS,
    CONF_LOAD_OVERRIDES,
    CONF_LOAD_TYPE,
    CONF_PORT,
    CONF_SYSTEM_VARIABLES,
    CONF_SYSVAR_NAME,
    CONF_SYSVAR_NUMBER,
    CONF_USE_MULTICAST,
    LOAD_TYPE_LIGHT,
    LOAD_TYPE_SWITCH,
    DEFAULT_EVENT_PORT,
    DEFAULT_PORT,
    DOMAIN,
    HARDWARE_MANUFACTURER,
    INTEGRATION_AUTHOR,
    INTEGRATION_AUTHOR_URL,
    PING_INTERVAL,
    SIGNAL_BUTTON_EVENT,
)
from .tpi import (
    ARC_LEVEL_MIXED,
    ARC_LEVEL_OFF,
    DALI_BROADCAST,
    DALI_GROUP_OFFSET,
    ColourState,
    ColourTempLimits,
    ColourType,
    DeviceColourFeatures,
    EventListener,
    EventType,
    GroupInfo,
    InstanceInfo,
    InstanceType,
    OccupancyTimerInfo,
    ProfileInfo,
    ProfileInformation,
    TpiClient,
    TpiEvent,
    TpiEventMode,
    ZenCommands,
    DaliCgTypeMask,
    group_to_address,
    is_group_address,
    parse_colour_payload,
)

_LOGGER = logging.getLogger(__name__)

# Coordinator update interval — primarily for the health-check ping.
# Real state updates arrive via push events.
SCAN_INTERVAL = timedelta(seconds=PING_INTERVAL)


# ---------------------------------------------------------------------------
# State containers
# ---------------------------------------------------------------------------

@dataclass
class OccupancySensorInfo:
    """Metadata for one occupancy sensor instance on a DALI control device."""
    cd_address: int          # Control device DALI address (64-127)
    instance_number: int     # Instance number on that device
    label: str = ""
    hold_time_s: int = 60    # How long to stay "occupied" after last detection


@dataclass
class ButtonInfo:
    """Metadata for one push-button instance on a DALI control device."""
    cd_address: int          # Control device DALI address (64-127)
    instance_number: int     # Instance number on that device
    label: str = ""
    has_led: bool = False    # Whether an LED state was reported for this button


@dataclass
class AbsoluteInputInfo:
    """Metadata for one absolute-input instance (dial/slider) on a control device."""
    cd_address: int          # Control device DALI address (64-127)
    instance_number: int     # Instance number on that device
    label: str = ""


@dataclass
class SystemVariableInfo:
    """Metadata for one controller system variable exposed as a sensor."""
    number: int              # System variable index (0-147)
    name: str = ""


@dataclass
class DeviceState:
    """Cached state for a single DALI address (group or short address)."""
    arc_level: int = ARC_LEVEL_OFF
    colour: ColourState | None = None
    last_scene: int | None = None


@dataclass
class ControllerState:
    """All discovered and live state for one zencontrol controller."""
    label: str = "zencontrol"
    version: tuple[int, int, int] = (0, 0, 0)

    # Groups  {group_number: GroupInfo}
    groups: dict[int, GroupInfo] = field(default_factory=dict)

    # Group capability data (auto-discovered from member short addresses)
    # {group_number: [short_address, ...]} — all members found in the TPI database
    group_members: dict[int, list[int]] = field(default_factory=dict)
    # {group_number: DeviceColourFeatures} — union of member colour features
    group_colour_features: dict[int, DeviceColourFeatures] = field(default_factory=dict)
    # {group_number: ColourTempLimits} — only populated when a tc member was found
    group_ct_limits: dict[int, ColourTempLimits] = field(default_factory=dict)

    # Profiles
    profile_info: ProfileInformation = field(default_factory=ProfileInformation)
    current_profile: int = 0

    # Live light states
    # Keyed by DALI *address* (group address = group_number + 64, or short addr 0-63)
    device_states: dict[int, DeviceState] = field(default_factory=dict)

    # Short address metadata (auto-discovered from the controller)
    short_addresses: list[int] = field(default_factory=list)
    # {address: cg_type_mask}
    short_address_types: dict[int, DaliCgTypeMask] = field(default_factory=dict)
    # {address: DeviceColourFeatures}
    short_address_colour_features: dict[int, DeviceColourFeatures] = field(default_factory=dict)
    # {address: label}
    short_address_labels: dict[int, str] = field(default_factory=dict)
    # {address: ColourTempLimits}
    short_address_ct_limits: dict[int, ColourTempLimits] = field(default_factory=dict)

    # Control-device labels {cd_address (64-127): label} — used as sub-device names
    cd_labels: dict[int, str] = field(default_factory=dict)

    # Occupancy sensors (auto-discovered)
    # List of all discovered occupancy sensor instances
    occupancy_sensors: list[OccupancySensorInfo] = field(default_factory=list)
    # Live occupancy state keyed by (cd_address, instance_number)
    sensor_occupancy: dict[tuple[int, int], bool] = field(default_factory=dict)

    # Push buttons (auto-discovered)
    buttons: list[ButtonInfo] = field(default_factory=list)
    # Last-known LED state keyed by (cd_address, instance_number)
    button_led_state: dict[tuple[int, int], bool] = field(default_factory=dict)

    # Absolute inputs / dials (auto-discovered)
    absolute_inputs: list[AbsoluteInputInfo] = field(default_factory=list)
    # Live absolute input value keyed by (cd_address, instance_number)
    absolute_values: dict[tuple[int, int], int] = field(default_factory=dict)

    # System variables (auto-discovered by name + manually configured)
    system_variables: list[SystemVariableInfo] = field(default_factory=list)
    # Live value keyed by variable number (float = raw int32 * 10**magnitude)
    system_variable_values: dict[int, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class ZenControlCoordinator(DataUpdateCoordinator[ControllerState]):
    """Manages one zencontrol Application Controller.

    Responsibilities:
    - Connects to the controller via UDP/TCP.
    - Discovers groups, scenes, and profiles on startup.
    - Registers itself with the shared EventListener and enables TPI events.
    - Handles push events from the controller to update entity state.
    - Periodically pings the controller; re-asserts event config if needed.
    - Exposes ZenCommands for entity service calls.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, entry_data: dict[str, Any]) -> None:
        self._entry_id = entry_id
        self._host: str = entry_data[CONF_HOST]
        self._port: int = entry_data.get(CONF_PORT, DEFAULT_PORT)
        self._event_port: int = entry_data.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT)
        self._use_multicast: bool = entry_data.get(CONF_USE_MULTICAST, False)
        # Manually configured system variables: list of {number, name}
        self._manual_sysvars: list[dict] = entry_data.get(CONF_SYSTEM_VARIABLES, [])
        # Manual entity-type overrides keyed by short address: {addr: override_dict}
        self._load_overrides: dict[int, dict] = {
            o[CONF_LOAD_ADDRESS]: o
            for o in entry_data.get(CONF_LOAD_OVERRIDES, [])
            if CONF_LOAD_ADDRESS in o
        }

        self._client = TpiClient(host=self._host, port=self._port)
        self.commands = ZenCommands(self._client)
        # Occupancy hold timers: (cd_address, instance_number) → cancel_callback
        self._occupancy_timers: dict[tuple[int, int], Any] = {}
        # Shared event listener we registered with (set in setup_events)
        self._listener: EventListener | None = None
        # True once initial discovery has completed successfully
        self._discovered = False
        # Cap concurrent TPI queries during discovery — parallel enough to
        # collapse timeout stacking, gentle enough not to flood the controller.
        self._query_sem = asyncio.Semaphore(16)

        super().__init__(
            hass,
            _LOGGER,
            name=f"zencontrol {self._host}",
            update_interval=SCAN_INTERVAL,
        )
        # data is initialised by DataUpdateCoordinator to None until first fetch
        self.data = ControllerState()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator overrides
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> ControllerState:
        """Called on startup and every SCAN_INTERVAL seconds.

        On first call: full discovery.
        On subsequent calls: health-check ping + re-assert events if needed.
        """
        if not self._client.connected:
            try:
                await self._client.connect()
            except OSError as exc:
                raise UpdateFailed(f"Cannot connect to {self._host}: {exc}") from exc

        if not self._discovered:
            await self._discover()
        else:
            # Health check — re-assert event configuration if controller rebooted
            await self._check_and_assert_events()

        return self.data

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def _discover(self) -> None:
        """Query controller metadata and auto-discover groups/scenes/profiles."""
        _LOGGER.debug("Starting discovery for %s", self._host)

        # Single non-blocking readiness check. If the controller hasn't finished
        # booting yet, raise UpdateFailed so HA retries via ConfigEntryNotReady
        # with exponential backoff — no sleeping on the event loop.
        if not await self.commands.query_startup_complete():
            raise UpdateFailed(
                f"Controller {self._host} is not ready yet — will retry"
            )

        # Basic controller info
        label = await self.commands.query_controller_label()
        self.data.label = label or self._host

        version = await self.commands.query_controller_version()
        if version:
            self.data.version = version
            _LOGGER.info(
                "Connected to '%s' firmware v%d.%d.%d",
                self.data.label,
                *version,
            )

        # Groups
        groups = await self.commands.query_groups()
        for group in groups:
            self.data.groups[group.number] = group
            # Initialise state entry for this group address
            addr = group_to_address(group.number)
            if addr not in self.data.device_states:
                self.data.device_states[addr] = DeviceState()

        # Profiles — query info (list + current from PROFILE_INFORMATION),
        # then confirm current with the dedicated QUERY_CURRENT_PROFILE_NUMBER
        # which is reliable even when the controller is running on schedule.
        self.data.profile_info = await self.commands.query_profile_information()
        self.data.current_profile = self.data.profile_info.current_profile
        current = await self.commands.query_current_profile_number()
        if current is not None:
            self.data.current_profile = current

        # Short addresses (auto-discovered)
        await self._discover_short_addresses()

        # Derive group colour capabilities from member short-address features.
        # QUERY_DALI_COLOUR_FEATURES is a per-device command that does not work on
        # group addresses, so we infer group capabilities by querying the group
        # membership of each short address and unioning their features.
        await self._discover_group_colour_features()

        # Control-device instances: occupancy sensors, push buttons, absolute inputs
        await self._discover_instances()

        # System variables (auto-discovered by name + manually configured)
        await self._discover_system_variables()

        # Initial state poll for all known addresses
        await self._poll_all_states()

        self._discovered = True
        _LOGGER.debug(
            "Discovery complete for %s: %d groups, %d short addresses, %d profiles, "
            "%d occupancy sensors, %d buttons, %d absolute inputs, %d system variables",
            self._host,
            len(self.data.groups),
            len(self.data.short_addresses),
            len(self.data.profile_info.profiles),
            len(self.data.occupancy_sensors),
            len(self.data.buttons),
            len(self.data.absolute_inputs),
            len(self.data.system_variables),
        )

    async def _bounded(self, coro):
        """Await *coro* under the discovery concurrency semaphore."""
        async with self._query_sem:
            return await coro

    async def _discover_short_addresses(self) -> None:
        """Auto-discover all DALI short addresses and query their metadata.

        Addresses are queried concurrently (bounded by the semaphore) so one
        non-answering device costs a single timeout, not one per query stacked
        serially across the whole line.
        """
        addresses = await self.commands.query_control_gear_addresses()
        self.data.short_addresses = addresses
        _LOGGER.debug("Discovered %d short addresses on %s: %s", len(addresses), self._host, addresses)

        await asyncio.gather(
            *(self._bounded(self._query_short_address_metadata(addr)) for addr in addresses)
        )

    async def _query_short_address_metadata(self, addr: int) -> None:
        """Fetch all metadata for one short address (sequential per device)."""
        cg_type = await self.commands.query_cg_type(addr)
        self.data.short_address_types[addr] = cg_type

        features = await self.commands.query_colour_features(addr)
        self.data.short_address_colour_features[addr] = features

        label = await self.commands.query_device_label(addr) or f"Light {addr}"
        self.data.short_address_labels[addr] = label

        if features.tc:
            limits = await self.commands.query_colour_temp_limits(addr)
            if limits:
                self.data.short_address_ct_limits[addr] = limits

        if addr not in self.data.device_states:
            self.data.device_states[addr] = DeviceState()

    async def _discover_group_colour_features(self) -> None:
        """Derive colour capabilities for each group from its member short addresses.

        QUERY_DALI_COLOUR_FEATURES only works on short addresses (0-63). We infer
        group capabilities by querying each short address for its group memberships,
        building a reverse map (group → members), then unioning the per-member
        DeviceColourFeatures and intersecting the CT limits of TC-capable members.
        """
        if not self.data.groups:
            return

        # Query every known short address for group membership (concurrently)
        # → full reverse map. Needed for both colour-feature derivation and
        # relay-only detection.
        memberships = await asyncio.gather(
            *(
                self._bounded(self.commands.query_group_membership(addr))
                for addr in self.data.short_addresses
            )
        )
        all_memberships: dict[int, frozenset[int]] = {
            addr: frozenset(groups)
            for addr, groups in zip(self.data.short_addresses, memberships)
        }

        # group_number → member short addresses
        group_members: dict[int, list[int]] = {}
        for addr, addr_groups in all_memberships.items():
            for gnum in addr_groups:
                if gnum in self.data.groups:
                    group_members.setdefault(gnum, []).append(addr)
        self.data.group_members = group_members

        # group_number → union of member colour features (colour-capable members only)
        group_features: dict[int, DeviceColourFeatures] = {}
        for addr, addr_groups in all_memberships.items():
            features = self.data.short_address_colour_features.get(addr)
            if not (features and features.supports_colour):
                continue
            for gnum in addr_groups:
                if gnum not in self.data.groups:
                    continue
                existing = group_features.get(gnum)
                if existing is None:
                    group_features[gnum] = DeviceColourFeatures(
                        xy=features.xy,
                        tc=features.tc,
                        primaries=features.primaries,
                        rgbwaf_channels=features.rgbwaf_channels,
                    )
                else:
                    group_features[gnum] = DeviceColourFeatures(
                        xy=existing.xy or features.xy,
                        tc=existing.tc or features.tc,
                        primaries=max(existing.primaries, features.primaries),
                        rgbwaf_channels=max(existing.rgbwaf_channels, features.rgbwaf_channels),
                    )
        self.data.group_colour_features = group_features

        # For TC-capable groups, CT limits = intersection (common range) across
        # TC-capable members so no fixture receives an out-of-range value.
        for gnum, features in group_features.items():
            if not features.tc:
                continue
            member_limits = [
                self.data.short_address_ct_limits[addr]
                for addr in group_members.get(gnum, [])
                if self.data.short_address_colour_features.get(
                    addr, DeviceColourFeatures()
                ).tc
                and addr in self.data.short_address_ct_limits
            ]
            if member_limits:
                self.data.group_ct_limits[gnum] = ColourTempLimits(
                    physical_warmest_k=max(l.physical_warmest_k for l in member_limits),
                    physical_coolest_k=min(l.physical_coolest_k for l in member_limits),
                    soft_warmest_k=max(l.soft_warmest_k for l in member_limits),
                    soft_coolest_k=min(l.soft_coolest_k for l in member_limits),
                    step_k=max(l.step_k for l in member_limits),
                )

        _LOGGER.debug(
            "Group capabilities on %s: members=%s features=%s",
            self._host, group_members,
            {g: vars(f) for g, f in group_features.items()},
        )

    async def _discover_system_variables(self) -> None:
        """Discover system variables to expose as sensors.

        Auto-discovers variables that have a name (Pro controllers answer
        QUERY_SYSTEM_VARIABLE_NAME; non-Pro controllers do not, yielding none),
        then merges any manually configured variables from the options flow.
        Values are populated from SYSTEM_VARIABLE_CHANGED events, so entities
        read "unknown" until each variable next changes.
        """
        max_sysvars = 148  # V2.1 Pro count; non-Pro simply returns None beyond 48
        by_number: dict[int, SystemVariableInfo] = {}

        # Auto: query all names concurrently; keep the ones that are named.
        names = await asyncio.gather(
            *(
                self._bounded(self.commands.query_system_variable_name(n))
                for n in range(max_sysvars)
            )
        )
        for number, name in enumerate(names):
            if name:
                by_number[number] = SystemVariableInfo(number=number, name=name)

        # Manual: merge configured variables (override/extend the auto entries).
        for cfg in self._manual_sysvars:
            try:
                number = int(cfg[CONF_SYSVAR_NUMBER])
            except (KeyError, ValueError, TypeError):
                continue
            name = str(cfg.get(CONF_SYSVAR_NAME) or "").strip()
            if not name:
                auto = by_number.get(number)
                name = auto.name if auto else f"System Variable {number}"
            by_number[number] = SystemVariableInfo(number=number, name=name)

        self.data.system_variables = [by_number[n] for n in sorted(by_number)]
        _LOGGER.debug(
            "Discovered %d system variables on %s: %s",
            len(self.data.system_variables), self._host,
            {sv.number: sv.name for sv in self.data.system_variables},
        )

    async def _discover_instances(self) -> None:
        """Auto-discover all DALI control-device instances.

        Control devices are walked concurrently (bounded by the semaphore);
        instances within one device stay sequential. Each instance is
        dispatched by type — occupancy sensors, push buttons, and absolute
        inputs. Other instance types are ignored.
        """
        cd_addresses = await self.commands.query_addresses_with_instances()
        _LOGGER.debug(
            "Found %d control devices with instances on %s: %s",
            len(cd_addresses), self._host, cd_addresses,
        )

        await asyncio.gather(
            *(self._bounded(self._discover_cd_instances(cd)) for cd in cd_addresses)
        )

        # Concurrent walks append in nondeterministic order — sort for stable
        # entity ordering across restarts.
        self.data.occupancy_sensors.sort(key=lambda s: (s.cd_address, s.instance_number))
        self.data.buttons.sort(key=lambda b: (b.cd_address, b.instance_number))
        self.data.absolute_inputs.sort(key=lambda a: (a.cd_address, a.instance_number))

    async def _discover_cd_instances(self, cd_addr: int) -> None:
        """Discover one control device: its label, then all of its instances."""
        # The device label doubles as the HA sub-device name for this CD.
        label = await self.commands.query_device_label(cd_addr)
        if label:
            self.data.cd_labels[cd_addr] = label

        instances = await self.commands.query_instances_by_address(cd_addr)
        for inst in instances:
            if inst.instance_type == InstanceType.OCCUPANCY_SENSOR:
                await self._add_occupancy_sensor(cd_addr, inst, instances)
            elif inst.instance_type == InstanceType.PUSH_BUTTON:
                await self._add_button(cd_addr, inst, instances)
            elif inst.instance_type == InstanceType.ABSOLUTE_INPUT:
                await self._add_absolute_input(cd_addr, inst, instances)

    async def _instance_label(
        self, cd_addr: int, inst: InstanceInfo, instances: list[InstanceInfo],
        suffix: str,
    ) -> str:
        """Resolve a display label for an instance.

        Prefers the instance's own label; otherwise falls back to *suffix*,
        appending the instance number when a device has more than one instance
        of the same type. The owning control device provides context via its
        HA sub-device name, so labels no longer embed the device label.
        """
        label = await self.commands.query_instance_label(cd_addr, inst.instance_number)
        if label:
            return label
        same_type = sum(
            1 for i in instances if i.instance_type == inst.instance_type
        )
        if same_type > 1:
            return f"{suffix} {inst.instance_number}"
        return suffix

    async def _add_occupancy_sensor(
        self, cd_addr: int, inst: InstanceInfo, instances: list[InstanceInfo],
    ) -> None:
        """Register one occupancy sensor instance and seed its initial state."""
        label = await self._instance_label(cd_addr, inst, instances, "Occupancy")
        timer = await self.commands.query_occupancy_timer(cd_addr, inst.instance_number)

        sensor = OccupancySensorInfo(
            cd_address=cd_addr,
            instance_number=inst.instance_number,
            label=label,
            hold_time_s=timer.hold_time_s,
        )
        self.data.occupancy_sensors.append(sensor)

        # Set initial state: occupied if the hold timer hasn't expired yet
        key = (cd_addr, inst.instance_number)
        occupied = timer.last_detect_s < timer.hold_time_s
        self.data.sensor_occupancy[key] = occupied
        _LOGGER.debug(
            "Occupancy sensor: addr=%d inst=%d label='%s' hold=%ds last_detect=%ds → %s",
            cd_addr, inst.instance_number, label,
            timer.hold_time_s, timer.last_detect_s,
            "occupied" if occupied else "clear",
        )

        # If currently occupied, start the hold timer for the remaining time
        if occupied:
            remaining = max(1, timer.hold_time_s - timer.last_detect_s)
            self._start_occupancy_timer(key, remaining)

    async def _add_button(
        self, cd_addr: int, inst: InstanceInfo, instances: list[InstanceInfo],
    ) -> None:
        """Register one push-button instance and query its initial LED state."""
        label = await self._instance_label(cd_addr, inst, instances, "Button")

        # Query the last-known LED state. A non-None result means the button
        # has a controller-managed LED; None means unknown/none.
        led = await self.commands.query_button_led_state(cd_addr, inst.instance_number)
        key = (cd_addr, inst.instance_number)
        if led is not None:
            self.data.button_led_state[key] = led

        self.data.buttons.append(
            ButtonInfo(
                cd_address=cd_addr,
                instance_number=inst.instance_number,
                label=label,
                has_led=led is not None,
            )
        )
        _LOGGER.debug(
            "Push button: addr=%d inst=%d label='%s' led=%s",
            cd_addr, inst.instance_number, label,
            "unknown" if led is None else ("on" if led else "off"),
        )

    async def _add_absolute_input(
        self, cd_addr: int, inst: InstanceInfo, instances: list[InstanceInfo],
    ) -> None:
        """Register one absolute-input (dial/slider) instance."""
        label = await self._instance_label(cd_addr, inst, instances, "Input")
        self.data.absolute_inputs.append(
            AbsoluteInputInfo(
                cd_address=cd_addr,
                instance_number=inst.instance_number,
                label=label,
            )
        )
        _LOGGER.debug(
            "Absolute input: addr=%d inst=%d label='%s'",
            cd_addr, inst.instance_number, label,
        )

    async def _poll_all_states(self) -> None:
        """Poll the current arc level (and colour) for all known addresses.

        Addresses are polled concurrently (bounded by the semaphore).
        """
        addresses_to_poll: list[int] = []

        # Group addresses (64-79)
        for gnum in self.data.groups:
            addresses_to_poll.append(group_to_address(gnum))

        # Short addresses
        addresses_to_poll.extend(self.data.short_addresses)

        await asyncio.gather(
            *(self._bounded(self._poll_address_state(addr)) for addr in addresses_to_poll)
        )

    async def _poll_address_state(self, addr: int) -> None:
        """Poll arc level (and colour, where applicable) for one address."""
        level = await self.commands.query_level(addr)
        if level is not None:
            state = self.data.device_states.setdefault(addr, DeviceState())
            state.arc_level = level

        # Poll colour for colour-capable short addresses
        if addr < DALI_GROUP_OFFSET:
            features = self.data.short_address_colour_features.get(addr)
            if features and features.supports_colour:
                colour = await self.commands.query_colour(addr)
                if colour:
                    self.data.device_states[addr].colour = colour
        else:
            # Poll colour for group addresses too — groups may contain colour fixtures.
            # QUERY_DALI_COLOUR returns NO_ANSWER for non-colour groups; that's fine.
            colour = await self.commands.query_colour(addr)
            if colour:
                self.data.device_states[addr].colour = colour

    # ------------------------------------------------------------------
    # TPI event configuration
    # ------------------------------------------------------------------

    async def setup_events(self, listener: EventListener) -> None:
        """Register with the shared listener and configure the controller."""
        self._listener = listener
        listener.register(self._host, self._on_event)

        if not self._use_multicast:
            ha_ip = await self._get_ha_ip()
            if ha_ip:
                ok = await self.commands.configure_unicast_events(ha_ip, self._event_port)
                if ok:
                    _LOGGER.debug(
                        "Unicast events configured: %s → %s:%d",
                        self._host,
                        ha_ip,
                        self._event_port,
                    )
                else:
                    _LOGGER.warning(
                        "Failed to configure unicast events for %s", self._host
                    )
            else:
                _LOGGER.warning("Could not determine HA IP for unicast events")
        else:
            # Multicast — just enable events on the controller
            await self.commands.enable_events_unicast(TpiEventMode.ENABLED)

    async def _check_and_assert_events(self) -> None:
        """Ping controller; re-assert event config if it has rebooted."""
        state = await self.commands.query_event_emit_state()
        if state is None:
            _LOGGER.debug("No response from %s during ping", self._host)
            return
        expected_bit = int(TpiEventMode.ENABLED)
        if not (state & expected_bit):
            _LOGGER.info(
                "Controller %s events not enabled (state=0x%02X) — re-asserting",
                self._host,
                state,
            )
            ha_ip = await self._get_ha_ip()
            if ha_ip and not self._use_multicast:
                await self.commands.configure_unicast_events(ha_ip, self._event_port)
            else:
                await self.commands.enable_events_unicast(TpiEventMode.ENABLED)

    async def _get_ha_ip(self) -> str | None:
        """Resolve HA's outbound IP toward the controller."""
        try:
            # Try the HA network helper first
            from homeassistant.components.network import async_get_source_ip
            ip = await async_get_source_ip(self.hass, target_ip=self._host)
            if ip:
                return ip
        except Exception:
            pass

        # Fallback: open a UDP socket and read the local address
        try:
            sock = await asyncio.get_event_loop().run_in_executor(
                None, self._resolve_local_ip
            )
            return sock
        except Exception:
            return None

    def _resolve_local_ip(self) -> str | None:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((self._host, self._port))
                return s.getsockname()[0]
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    @callback
    def _on_event(self, source_ip: str, event: TpiEvent) -> None:
        """Dispatch an incoming TPI event to the appropriate state update."""
        try:
            if event.event_type == EventType.LEVEL_CHANGE_V2:
                self._handle_level_change_v2(event)
            elif event.event_type == EventType.LEVEL_CHANGE:
                self._handle_level_change(event)
            elif event.event_type == EventType.GROUP_LEVEL_CHANGE:
                self._handle_group_level_change(event)
            elif event.event_type == EventType.COLOUR_CHANGED:
                self._handle_colour_changed(event)
            elif event.event_type == EventType.SCENE_CHANGE:
                self._handle_scene_change(event)
            elif event.event_type == EventType.PROFILE_CHANGED:
                self._handle_profile_changed(event)
            elif event.event_type == EventType.OCCUPANCY:
                self._handle_occupancy(event)
            elif event.event_type == EventType.BUTTON_PRESS:
                self._handle_button(event, BUTTON_EVENT_PRESS)
            elif event.event_type == EventType.BUTTON_HOLD:
                self._handle_button(event, BUTTON_EVENT_HOLD)
            elif event.event_type == EventType.ABSOLUTE_INPUT:
                self._handle_absolute_input(event)
            elif event.event_type == EventType.SYSTEM_VARIABLE_CHANGED:
                self._handle_system_variable_changed(event)
        except Exception:
            _LOGGER.exception("Error handling TPI event type 0x%02X", event.event_type)

    def _handle_level_change_v2(self, event: TpiEvent) -> None:
        """LEVEL_CHANGE_EVENT_V2: target = address/group, data = [arc_level, dimming_to]."""
        addr = event.target
        if not event.data:
            return
        arc_level = event.data[0]
        state = self.data.device_states.setdefault(addr, DeviceState())
        state.arc_level = arc_level
        self.async_set_updated_data(self.data)

    def _handle_level_change(self, event: TpiEvent) -> None:
        """LEVEL_CHANGE_EVENT: target = address, data = [arc_level]."""
        addr = event.target
        if not event.data:
            return
        arc_level = event.data[0]
        state = self.data.device_states.setdefault(addr, DeviceState())
        # Only update if we don't have a V2 listener active (avoid double updates)
        state.arc_level = arc_level
        self.async_set_updated_data(self.data)

    def _handle_group_level_change(self, event: TpiEvent) -> None:
        """GROUP_LEVEL_CHANGE_EVENT: target = group number, data = [arc_level]."""
        group_num = event.target
        addr = group_to_address(group_num)
        if not event.data:
            return
        arc_level = event.data[0]
        state = self.data.device_states.setdefault(addr, DeviceState())
        state.arc_level = arc_level
        self.async_set_updated_data(self.data)

    def _handle_colour_changed(self, event: TpiEvent) -> None:
        """COLOUR_CHANGED_EVENT: target = address or group, data = [colour_type, ...]."""
        if len(event.data) < 1:
            return
        try:
            colour_type = ColourType(event.data[0])
        except ValueError:
            return
        state = self.data.device_states.setdefault(event.target, DeviceState())
        state.colour = parse_colour_payload(colour_type, event.data[1:])
        self.async_set_updated_data(self.data)

    def _handle_scene_change(self, event: TpiEvent) -> None:
        """SCENE_CHANGE_EVENT: target = address, data = [last_scene, at_scene]."""
        addr = event.target
        if not event.data:
            return
        scene_num = event.data[0]
        state = self.data.device_states.setdefault(addr, DeviceState())
        state.last_scene = scene_num
        self.async_set_updated_data(self.data)

    def _handle_profile_changed(self, event: TpiEvent) -> None:
        """PROFILE_CHANGED_EVENT: data = [profile_hi, profile_lo]."""
        if len(event.data) < 2:
            return
        profile_id = (event.data[0] << 8) | event.data[1]
        self.data.current_profile = profile_id
        self.async_set_updated_data(self.data)

    def _handle_occupancy(self, event: TpiEvent) -> None:
        """OCCUPANCY_EVENT: target = cd_address, data = [instance_number, ...]."""
        if not event.data:
            return
        cd_address = event.target
        instance_number = event.data[0]
        key = (cd_address, instance_number)

        # Look up hold time for this sensor (fall back to 60 s)
        hold_time_s = 60
        for sensor in self.data.occupancy_sensors:
            if sensor.cd_address == cd_address and sensor.instance_number == instance_number:
                hold_time_s = sensor.hold_time_s
                break

        # Mark occupied and restart the hold timer
        self.data.sensor_occupancy[key] = True
        self._start_occupancy_timer(key, hold_time_s)
        self.async_set_updated_data(self.data)

    def _handle_button(self, event: TpiEvent, event_type: str) -> None:
        """BUTTON_PRESS/HOLD_EVENT: target = cd_address, data = [instance_number].

        Buttons are transient events, not state. We fan out to the matching
        `event` entity and any device-trigger automations via a dispatcher
        signal — no coordinator data update.
        """
        if not event.data:
            return
        cd_address = event.target
        instance_number = event.data[0]
        _LOGGER.debug(
            "Button %s: addr=%d inst=%d", event_type, cd_address, instance_number
        )
        async_dispatcher_send(
            self.hass,
            SIGNAL_BUTTON_EVENT.format(self._entry_id),
            cd_address,
            instance_number,
            event_type,
        )

    def _handle_absolute_input(self, event: TpiEvent) -> None:
        """ABSOLUTE_INPUT_EVENT: target = cd_address, data = [instance, hi, lo]."""
        if len(event.data) < 3:
            return
        cd_address = event.target
        instance_number = event.data[0]
        value = (event.data[1] << 8) | event.data[2]
        _LOGGER.debug(
            "Absolute input: addr=%d inst=%d value=%d", cd_address, instance_number, value
        )
        self.data.absolute_values[(cd_address, instance_number)] = value
        self.async_set_updated_data(self.data)

    def _handle_system_variable_changed(self, event: TpiEvent) -> None:
        """SYSTEM_VARIABLE_CHANGED_EVENT: target = variable index,
        data = [int32 value (big-endian), int8 magnitude]. value = raw * 10**magnitude.
        """
        if len(event.data) < 5:
            return
        number = event.target
        raw = int.from_bytes(event.data[0:4], "big", signed=True)
        magnitude = int.from_bytes(event.data[4:5], "big", signed=True)
        value = raw * (10 ** magnitude)
        _LOGGER.debug(
            "System variable %d changed: raw=%d magnitude=%d value=%s",
            number, raw, magnitude, value,
        )
        self.data.system_variable_values[number] = value
        self.async_set_updated_data(self.data)

    def _start_occupancy_timer(self, key: tuple[int, int], delay_s: int) -> None:
        """Cancel any existing hold timer for *key* and start a new one."""
        cancel = self._occupancy_timers.pop(key, None)
        if cancel is not None:
            cancel()
        self._occupancy_timers[key] = async_call_later(
            self.hass, delay_s, self._make_occupancy_timeout(key)
        )

    def _make_occupancy_timeout(self, key: tuple[int, int]):
        """Return a callback that clears occupancy for *key* when the hold timer fires."""
        @callback
        def _on_timeout(_now: Any) -> None:
            self._occupancy_timers.pop(key, None)
            self.data.sensor_occupancy[key] = False
            self.async_set_updated_data(self.data)
        return _on_timeout

    # ------------------------------------------------------------------
    # Helpers for entities
    # ------------------------------------------------------------------

    def get_device_state(self, address: int) -> DeviceState:
        return self.data.device_states.get(address, DeviceState())

    def load_override(self, address: int) -> dict | None:
        """Return the manual entity-type override for a short address, if any."""
        return self._load_overrides.get(address)

    def resolved_load_type(self, address: int) -> str:
        """Effective HA entity type for a short address.

        A manual override wins; otherwise fall back to capability detection —
        relay control gear becomes a switch, everything else a light. Each
        platform's setup filters on this so an address registers exactly once.
        """
        override = self._load_overrides.get(address)
        if override:
            return override[CONF_LOAD_TYPE]
        cg_type = self.data.short_address_types.get(address, DaliCgTypeMask(0))
        return LOAD_TYPE_SWITCH if DaliCgTypeMask.RELAY in cg_type else LOAD_TYPE_LIGHT

    @property
    def device_info(self) -> DeviceInfo:
        """DeviceInfo for the controller itself (parent of all sub-devices)."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self.data.label,
            manufacturer=HARDWARE_MANUFACTURER,
            sw_version="{}.{}.{}".format(*self.data.version),
            configuration_url=INTEGRATION_AUTHOR_URL,
            via_device=None,
        )

    def device_info_for_cd(self, cd_address: int) -> DeviceInfo:
        """Sub-device for a DALI control device (keypad, multisensor, …)."""
        label = self.data.cd_labels.get(cd_address) or f"Control Device {cd_address - 64}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_cd_{cd_address}")},
            name=label,
            manufacturer=HARDWARE_MANUFACTURER,
            model="DALI control device",
            via_device=(DOMAIN, self._entry_id),
        )

    def device_info_for_short_address(self, address: int) -> DeviceInfo:
        """Sub-device for a DALI control gear (fixture/relay) short address."""
        label = self.data.short_address_labels.get(address) or f"Light {address}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_sa_{address}")},
            name=label,
            manufacturer=HARDWARE_MANUFACTURER,
            model="DALI control gear",
            via_device=(DOMAIN, self._entry_id),
        )

    async def async_disconnect(self) -> None:
        """Disconnect from the controller and release event/timer resources."""
        # Stop receiving events for this controller — otherwise the shared
        # listener keeps dispatching into a dead coordinator after removal.
        if self._listener is not None:
            self._listener.unregister(self._host)
            self._listener = None
        for cancel in self._occupancy_timers.values():
            cancel()
        self._occupancy_timers.clear()
        await self._client.disconnect()

