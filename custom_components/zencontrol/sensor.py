"""zencontrol sensor entities — controller system variables."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN, UID_SYSVAR
from .coordinator import SystemVariableInfo, ZenControlCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a sensor entity for each discovered/configured system variable."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities = [
        ZenSystemVariableSensor(coordinator, entry, sysvar)
        for sysvar in coordinator.data.system_variables
    ]

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug(
            "No system variables found for %s — skipping sensor entities",
            coordinator.data.label,
        )


class ZenSystemVariableSensor(CoordinatorEntity[ZenControlCoordinator], SensorEntity):
    """Sensor reporting the value of a controller system variable.

    The value is the signed number reported by SYSTEM_VARIABLE_CHANGED events
    (raw int32 * 10**magnitude). The protocol carries no unit, so the sensor is
    unitless; users can assign a unit/device-class per entity in Home Assistant.
    State is unknown until the variable next changes (events are the only source
    of full-precision values).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        sysvar: SystemVariableInfo,
    ) -> None:
        super().__init__(coordinator)
        self._number = sysvar.number
        self._attr_name = sysvar.name or f"System Variable {sysvar.number}"
        self._attr_unique_id = f"{entry.entry_id}_{UID_SYSVAR}_{sysvar.number}"
        self._attr_device_info = coordinator.device_info
        self._attr_extra_state_attributes = {"variable_number": sysvar.number}

    @property
    def native_value(self) -> float | int | None:
        """Return the last-reported value, or None until the variable changes."""
        return self.coordinator.data.system_variable_values.get(self._number)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
