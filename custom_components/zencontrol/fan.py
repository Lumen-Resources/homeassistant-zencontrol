"""zencontrol fan entities — relay/dimmable loads overridden to 'fan'.

A fan ECG selects a speed from the DALI arc level. Speeds are exposed as named
preset modes, each mapped to an arc level (installer-configurable; sensible
defaults are generated when the fan is added). Only short addresses overridden
to type ``fan`` become fans (see coordinator.resolved_load_type).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FAN_SPEEDS,
    CONF_LOAD_NAME,
    DATA_COORDINATOR,
    DOMAIN,
    LOAD_TYPE_FAN,
    UID_FAN,
)
from .coordinator import DeviceState, ZenControlCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create fan entities for short addresses overridden to 'fan'."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities = [
        ZenFan(coordinator, entry, addr)
        for addr in coordinator.data.short_addresses
        if coordinator.resolved_load_type(addr) == LOAD_TYPE_FAN
    ]
    if entities:
        async_add_entities(entities)


class ZenFan(CoordinatorEntity[ZenControlCoordinator], FanEntity):
    """A DALI fan whose speed is selected by arc level, via named preset modes."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = None  # primary entity of its own device
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        address: int,
    ) -> None:
        super().__init__(coordinator)
        self._address = address
        cfg = coordinator.load_override(address) or {}
        # Speed table: [(name, arc_level), ...] ascending by level.
        self._speeds: list[tuple[str, int]] = sorted(
            (
                (str(s["name"]), int(s["level"]))
                for s in (cfg.get(CONF_FAN_SPEEDS) or [])
                if "name" in s and "level" in s
            ),
            key=lambda s: s[1],
        )
        self._attr_preset_modes = [name for name, _ in self._speeds]
        self._level_by_name = {name: level for name, level in self._speeds}

        self._attr_device_info = coordinator.device_info_for_short_address(address)
        name = cfg.get(CONF_LOAD_NAME)
        if name:
            self._attr_name = name
            self._attr_has_entity_name = False
        self._attr_unique_id = f"{entry.entry_id}_{UID_FAN}_{address}"
        self._attr_extra_state_attributes = {"dali_address": address}

    @property
    def _arc(self) -> int:
        return self.coordinator.get_device_state(self._address).arc_level

    @property
    def is_on(self) -> bool:
        return self._arc != 0

    @property
    def preset_mode(self) -> str | None:
        """Nearest configured speed to the current arc level (None when off)."""
        arc = self._arc
        if arc == 0 or not self._speeds:
            return None
        name, _ = min(self._speeds, key=lambda s: abs(s[1] - arc))
        return name

    async def _set_arc(self, level: int) -> None:
        await self.coordinator.commands.set_arc_level(self._address, level)
        state = self.coordinator.data.device_states.setdefault(self._address, DeviceState())
        state.arc_level = level
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        level = self._level_by_name.get(preset_mode)
        if level is not None:
            await self._set_arc(level)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        # Default to the lowest configured speed when none is requested.
        if preset_mode is None and self._attr_preset_modes:
            preset_mode = self._attr_preset_modes[0]
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_arc(0)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
