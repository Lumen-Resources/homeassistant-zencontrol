"""zencontrol cover entities — blinds on relay loads overridden to 'cover'.

Position maps onto the DALI arc level (0–254); the special arc level 255
(``COVER_STOP_ARC``) halts a moving blind. Only short addresses the user has
overridden to type ``cover`` become covers (see coordinator.resolved_load_type).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_COVER_DEVICE_CLASS,
    CONF_COVER_INVERT,
    CONF_LOAD_NAME,
    COVER_STOP_ARC,
    DATA_COORDINATOR,
    DOMAIN,
    LOAD_TYPE_COVER,
    UID_COVER,
)
from .coordinator import DeviceState, ZenControlCoordinator

_LOGGER = logging.getLogger(__name__)

_DALI_MAX = 254

_DEVICE_CLASSES = {
    "blind": CoverDeviceClass.BLIND,
    "shade": CoverDeviceClass.SHADE,
    "curtain": CoverDeviceClass.CURTAIN,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create cover entities for short addresses overridden to 'cover'."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities = [
        ZenCover(coordinator, entry, addr)
        for addr in coordinator.data.short_addresses
        if coordinator.resolved_load_type(addr) == LOAD_TYPE_COVER
    ]
    if entities:
        async_add_entities(entities)


class ZenCover(CoordinatorEntity[ZenControlCoordinator], CoverEntity):
    """A DALI blind driven by arc level: 0–254 = position, 255 = stop."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = None  # primary entity of its own device
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
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
        self._invert = bool(cfg.get(CONF_COVER_INVERT, False))
        self._attr_device_class = _DEVICE_CLASSES.get(
            cfg.get(CONF_COVER_DEVICE_CLASS, "blind"), CoverDeviceClass.BLIND
        )
        # A custom name overrides the DALI device label used for the device.
        name = cfg.get(CONF_LOAD_NAME)
        self._attr_device_info = coordinator.device_info_for_short_address(address)
        if name:
            self._attr_name = name
            self._attr_has_entity_name = False
        self._attr_unique_id = f"{entry.entry_id}_{UID_COVER}_{address}"
        self._attr_extra_state_attributes = {"dali_address": address}

    @property
    def _arc(self) -> int:
        return self.coordinator.get_device_state(self._address).arc_level

    @property
    def current_cover_position(self) -> int:
        """0 = closed, 100 = open (invert flips which arc end is 'open')."""
        pct = round(self._arc / _DALI_MAX * 100)
        return (100 - pct) if self._invert else pct

    @property
    def is_closed(self) -> bool:
        return self.current_cover_position == 0

    def _position_to_arc(self, position: int) -> int:
        pct = (100 - position) if self._invert else position
        return max(0, min(_DALI_MAX, round(pct / 100 * _DALI_MAX)))

    async def _drive_to(self, arc: int) -> None:
        await self.coordinator.commands.set_arc_level(self._address, arc)
        # Optimistic — real position follows via LEVEL_CHANGE events.
        state = self.coordinator.data.device_states.setdefault(self._address, DeviceState())
        state.arc_level = arc
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._drive_to(self._position_to_arc(100))

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._drive_to(self._position_to_arc(0))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._drive_to(self._position_to_arc(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        # Stop does not change the reported level — don't optimistically update.
        await self.coordinator.commands.set_arc_level(self._address, COVER_STOP_ARC)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
