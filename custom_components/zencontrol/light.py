"""zencontrol light entities — DALI groups and manually-added short addresses."""
from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    LOAD_TYPE_LIGHT,
    UID_GROUP,
    UID_SHORT,
)
from .coordinator import DeviceState, ZenControlCoordinator
from .tpi import (
    ARC_LEVEL_MAX,
    ARC_LEVEL_MIXED,
    ARC_LEVEL_OFF,
    DALI_BROADCAST,
    ColourState,
    ColourType,
    DaliCgTypeMask,
    DeviceColourFeatures,
    group_to_address,
)

_LOGGER = logging.getLogger(__name__)

# DALI arc level range: 1-254 (0 = off)
_DALI_MAX = 254
_HA_MAX = 255


def _arc_to_brightness(arc: int) -> int:
    """Convert DALI arc level (1-254) to HA brightness (1-255)."""
    if arc == ARC_LEVEL_OFF:
        return 0
    if arc == ARC_LEVEL_MIXED:
        return _HA_MAX
    return round(arc * _HA_MAX / _DALI_MAX)


def _brightness_to_arc(brightness: int) -> int:
    """Convert HA brightness (1-255) to DALI arc level (1-254)."""
    if brightness == 0:
        return ARC_LEVEL_OFF
    arc = round(brightness * _DALI_MAX / _HA_MAX)
    return max(1, min(_DALI_MAX, arc))


def _channel_to_dali(value: int) -> int:
    """Scale a HA colour channel (0-255) to DALI range (0-254).

    DALI reserves 0xFF (255) as 'no change' — valid channel values are 0-254.
    """
    return min(_DALI_MAX, round(value * _DALI_MAX / _HA_MAX))


# ---------------------------------------------------------------------------
# Colour-mode derivation (shared by group and short-address lights)
# ---------------------------------------------------------------------------

def _modes_from_features(f: DeviceColourFeatures) -> set[ColorMode]:
    """Supported colour modes implied by discovery-time colour features."""
    modes: set[ColorMode] = set()
    if f.tc:
        modes.add(ColorMode.COLOR_TEMP)
    if f.rgbwaf_channels >= 4:
        modes.add(ColorMode.RGBW)
    elif f.rgbwaf_channels >= 3:
        modes.add(ColorMode.RGB)
    if f.xy:
        modes.add(ColorMode.XY)
    return modes


def _mode_from_state(
    cs: ColourState | None, f: DeviceColourFeatures
) -> ColorMode | None:
    """Active colour mode implied by the live colour state, if any."""
    if cs and cs.colour_type:
        if cs.colour_type == ColourType.TC:
            return ColorMode.COLOR_TEMP
        if cs.colour_type == ColourType.RGBWAF:
            return ColorMode.RGBW if f.rgbwaf_channels >= 4 else ColorMode.RGB
        if cs.colour_type == ColourType.XY:
            return ColorMode.XY
    return None


def _mode_from_features(f: DeviceColourFeatures) -> ColorMode | None:
    """Fallback colour mode from features when no live colour state exists."""
    if f.tc and not f.xy and not f.rgbwaf_channels:
        return ColorMode.COLOR_TEMP
    if f.rgbwaf_channels >= 4:
        return ColorMode.RGBW
    if f.rgbwaf_channels >= 3:
        return ColorMode.RGB
    if f.xy:
        return ColorMode.XY
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create light entities for all groups and configured short addresses."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list[LightEntity] = []

    # --- Group lights (auto-discovered) ---
    for group_num, group_info in coordinator.data.groups.items():
        entities.append(ZenGroupLight(coordinator, entry, group_num))

    # --- Short address lights (resolved type == light) ---
    for addr in coordinator.data.short_addresses:
        if coordinator.resolved_load_type(addr) == LOAD_TYPE_LIGHT:
            entities.append(ZenShortAddressLight(coordinator, entry, addr))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class _ZenLightBase(CoordinatorEntity[ZenControlCoordinator], LightEntity):
    """Shared base for group and short-address light entities."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        dali_address: int,
        unique_id_prefix: str,
        name: str | None,
        device_info,
    ) -> None:
        super().__init__(coordinator)
        self._dali_address = dali_address
        self._attr_unique_id = f"{entry.entry_id}_{unique_id_prefix}_{dali_address}"
        self._attr_name = name
        self._attr_device_info = device_info

    # ------------------------------------------------------------------
    # State from coordinator
    # ------------------------------------------------------------------

    @property
    def _device_state(self) -> DeviceState:
        return self.coordinator.get_device_state(self._dali_address)

    @property
    def is_on(self) -> bool:
        return self._device_state.arc_level != ARC_LEVEL_OFF

    @property
    def brightness(self) -> int | None:
        arc = self._device_state.arc_level
        if arc == ARC_LEVEL_OFF:
            return 0
        return _arc_to_brightness(arc)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Colour helpers
    # ------------------------------------------------------------------

    def _get_colour_state(self) -> ColourState | None:
        return self._device_state.colour

    @property
    def color_temp_kelvin(self) -> int | None:
        cs = self._get_colour_state()
        if cs and cs.colour_type == ColourType.TC:
            return cs.kelvin
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        cs = self._get_colour_state()
        if cs and cs.colour_type == ColourType.RGBWAF:
            if cs.r is not None and cs.g is not None and cs.b is not None:
                return (
                    round(cs.r * _HA_MAX / _DALI_MAX),
                    round(cs.g * _HA_MAX / _DALI_MAX),
                    round(cs.b * _HA_MAX / _DALI_MAX),
                )
        return None

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        cs = self._get_colour_state()
        if cs and cs.colour_type == ColourType.RGBWAF:
            if None not in (cs.r, cs.g, cs.b, cs.w):
                return (
                    round(cs.r * _HA_MAX / _DALI_MAX),  # type: ignore[operator]
                    round(cs.g * _HA_MAX / _DALI_MAX),  # type: ignore[operator]
                    round(cs.b * _HA_MAX / _DALI_MAX),  # type: ignore[operator]
                    round(cs.w * _HA_MAX / _DALI_MAX),  # type: ignore[operator]
                )
        return None

    @property
    def xy_color(self) -> tuple[float, float] | None:
        cs = self._get_colour_state()
        if cs and cs.colour_type == ColourType.XY:
            if cs.x is not None and cs.y is not None:
                # Convert from 0-0xFFFE integer range to 0.0-1.0 float
                return (cs.x / 0xFFFE, cs.y / 0xFFFE)
        return None

    # ------------------------------------------------------------------
    # Turn on/off  (optimistic state update — don't wait for push event)
    # ------------------------------------------------------------------

    def _resolve_arc(self, brightness: int | None) -> int:
        """Return the arc level for a command, or 0xFF for colour-only (no arc change)."""
        return _brightness_to_arc(brightness) if brightness is not None else 0xFF

    def _arc_after_colour(self, arc: int) -> int:
        """Resolve the effective arc level to report after a colour command.

        When arc=0xFF (colour-only), preserve the existing level — but if the
        device was off, assume it came on at max so the icon reflects reality.
        """
        if arc != 0xFF:
            return arc
        current = self._device_state.arc_level
        return current if current != ARC_LEVEL_OFF else ARC_LEVEL_MAX

    def _optimistic_update(
        self,
        new_arc: int | None = None,
        new_colour: ColourState | None = None,
    ) -> None:
        """Apply an immediate optimistic state update and repaint the entity."""
        state = self.coordinator.data.device_states.setdefault(
            self._dali_address, DeviceState()
        )
        if new_arc is not None:
            state.arc_level = new_arc
        if new_colour is not None:
            state.colour = new_colour
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        transition = kwargs.get(ATTR_TRANSITION)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        rgbw = kwargs.get(ATTR_RGBW_COLOR)
        xy = kwargs.get(ATTR_XY_COLOR)

        cmds = self.coordinator.commands
        addr = self._dali_address

        if kelvin is not None:
            arc = self._resolve_arc(brightness)
            await cmds.set_colour_tc(addr, kelvin, arc_level=arc)
            self._optimistic_update(
                new_arc=self._arc_after_colour(arc),
                new_colour=ColourState(colour_type=ColourType.TC, kelvin=kelvin),
            )
            return

        if rgbw is not None:
            arc = self._resolve_arc(brightness)
            r, g, b, w = [_channel_to_dali(v) for v in rgbw]
            await cmds.set_colour_rgb(addr, r, g, b, w=w, arc_level=arc)
            self._optimistic_update(
                new_arc=self._arc_after_colour(arc),
                new_colour=ColourState(colour_type=ColourType.RGBWAF, r=r, g=g, b=b, w=w),
            )
            return

        if rgb is not None:
            arc = self._resolve_arc(brightness)
            r, g, b = [_channel_to_dali(v) for v in rgb]
            await cmds.set_colour_rgb(addr, r, g, b, arc_level=arc)
            self._optimistic_update(
                new_arc=self._arc_after_colour(arc),
                new_colour=ColourState(colour_type=ColourType.RGBWAF, r=r, g=g, b=b),
            )
            return

        if xy is not None:
            arc = self._resolve_arc(brightness)
            x_int = round(xy[0] * 0xFFFE)
            y_int = round(xy[1] * 0xFFFE)
            await cmds.set_colour_xy(addr, x_int, y_int, arc_level=arc)
            self._optimistic_update(
                new_arc=self._arc_after_colour(arc),
                new_colour=ColourState(colour_type=ColourType.XY, x=x_int, y=y_int),
            )
            return

        if brightness is not None:
            arc = _brightness_to_arc(brightness)
            if transition is not None and transition > 0:
                await cmds.custom_fade(addr, arc, int(transition))
            else:
                await cmds.set_arc_level(addr, arc)
            self._optimistic_update(new_arc=arc)
            return

        await cmds.recall_max(addr)
        self._optimistic_update(new_arc=ARC_LEVEL_MAX)

    async def async_turn_off(self, **kwargs: Any) -> None:
        transition = kwargs.get(ATTR_TRANSITION)
        if transition is not None and transition > 0:
            await self.coordinator.commands.custom_fade(
                self._dali_address, ARC_LEVEL_OFF, int(transition)
            )
        else:
            await self.coordinator.commands.set_off(self._dali_address)
        self._optimistic_update(new_arc=ARC_LEVEL_OFF)


# ---------------------------------------------------------------------------
# Group light
# ---------------------------------------------------------------------------

class ZenGroupLight(_ZenLightBase):
    """Light entity representing a DALI group (auto-discovered)."""

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        group_number: int,
    ) -> None:
        group_info = coordinator.data.groups[group_number]
        address = group_to_address(group_number)
        # Groups are logical constructs — they live on the controller device.
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            dali_address=address,
            unique_id_prefix=UID_GROUP,
            name=group_info.label,
            device_info=coordinator.device_info,
        )
        self._group_number = group_number
        self._attr_extra_state_attributes = {"group_number": group_number}

    @property
    def _features(self) -> DeviceColourFeatures:
        """Colour features inferred for this group from its members at discovery."""
        return self.coordinator.data.group_colour_features.get(
            self._group_number, DeviceColourFeatures()
        )

    @property
    def _any_dimmable(self) -> bool:
        """True if any member of this group is dimmable (i.e. not relay-only)."""
        members = self.coordinator.data.group_members.get(self._group_number, [])
        types = self.coordinator.data.short_address_types
        return any(
            DaliCgTypeMask.RELAY not in types.get(addr, DaliCgTypeMask(0))
            for addr in members
        )

    @property
    def min_color_temp_kelvin(self) -> int | None:
        limits = self.coordinator.data.group_ct_limits.get(self._group_number)
        return limits.soft_warmest_k if limits else None

    @property
    def max_color_temp_kelvin(self) -> int | None:
        limits = self.coordinator.data.group_ct_limits.get(self._group_number)
        return limits.soft_coolest_k if limits else None

    @property
    def brightness(self) -> int | None:
        # Relay-only groups are ONOFF and must not report a brightness value.
        return None if not self._any_dimmable else super().brightness

    @property
    def color_mode(self) -> ColorMode:
        if not self._any_dimmable:
            return ColorMode.ONOFF
        f = self._features
        return (
            _mode_from_state(self._get_colour_state(), f)
            or _mode_from_features(f)
            or ColorMode.BRIGHTNESS
        )

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        if not self._any_dimmable:
            return {ColorMode.ONOFF}

        modes = _modes_from_features(self._features)

        # Also include any mode implied by the current colour state, so the set is
        # never narrower than color_mode even if feature discovery came back empty.
        cs = self._get_colour_state()
        if cs and cs.colour_type:
            if cs.colour_type == ColourType.TC:
                modes.add(ColorMode.COLOR_TEMP)
            elif cs.colour_type == ColourType.RGBWAF:
                modes.add(ColorMode.RGBW if cs.w is not None else ColorMode.RGB)
            elif cs.colour_type == ColourType.XY:
                modes.add(ColorMode.XY)

        return modes or {ColorMode.BRIGHTNESS}

    @property
    def supported_features(self) -> LightEntityFeature:
        return LightEntityFeature(0) if not self._any_dimmable else LightEntityFeature.TRANSITION

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Relay-only groups support only on/off — ignore brightness/colour kwargs.
        if not self._any_dimmable:
            await self.coordinator.commands.recall_max(self._dali_address)
            self._optimistic_update(new_arc=ARC_LEVEL_MAX)
            return
        await super().async_turn_on(**kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        # Relays can't fade — ignore transition, mirroring async_turn_on.
        if not self._any_dimmable:
            await self.coordinator.commands.set_off(self._dali_address)
            self._optimistic_update(new_arc=ARC_LEVEL_OFF)
            return
        await super().async_turn_off(**kwargs)


# ---------------------------------------------------------------------------
# Short address light
# ---------------------------------------------------------------------------

class ZenShortAddressLight(_ZenLightBase):
    """Light entity for a manually-configured DALI short address."""

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        address: int,
    ) -> None:
        # Each fixture is its own HA device; name=None makes this the primary
        # entity, so it takes the device's name (the DALI device label).
        super().__init__(
            coordinator=coordinator,
            entry=entry,
            dali_address=address,
            unique_id_prefix=UID_SHORT,
            name=None,
            device_info=coordinator.device_info_for_short_address(address),
        )
        self._attr_extra_state_attributes = {"dali_address": address}

    @property
    def _features(self) -> DeviceColourFeatures:
        return self.coordinator.data.short_address_colour_features.get(
            self._dali_address, DeviceColourFeatures()
        )

    @property
    def min_color_temp_kelvin(self) -> int | None:
        """Warmest (lowest K) supported colour temperature."""
        limits = self.coordinator.data.short_address_ct_limits.get(self._dali_address)
        return limits.soft_warmest_k if limits else None

    @property
    def max_color_temp_kelvin(self) -> int | None:
        """Coolest (highest K) supported colour temperature."""
        limits = self.coordinator.data.short_address_ct_limits.get(self._dali_address)
        return limits.soft_coolest_k if limits else None

    @property
    def color_mode(self) -> ColorMode:
        f = self._features
        return (
            _mode_from_state(self._get_colour_state(), f)
            or _mode_from_features(f)
            or ColorMode.BRIGHTNESS
        )

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return _modes_from_features(self._features) or {ColorMode.BRIGHTNESS}

    @property
    def supported_features(self) -> LightEntityFeature:
        return LightEntityFeature.TRANSITION
