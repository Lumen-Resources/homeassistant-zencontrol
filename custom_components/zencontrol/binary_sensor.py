"""zencontrol binary sensor entities — occupancy sensors and absolute inputs."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN, UID_ABSOLUTE, UID_OCCUPANCY
from .coordinator import (
    AbsoluteInputInfo,
    OccupancySensorInfo,
    ZenControlCoordinator,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create binary sensors: occupancy sensors and absolute inputs."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities: list[BinarySensorEntity] = [
        ZenOccupancySensor(coordinator, entry, sensor)
        for sensor in coordinator.data.occupancy_sensors
    ]
    entities.extend(
        ZenAbsoluteInputBinarySensor(coordinator, entry, absinput)
        for absinput in coordinator.data.absolute_inputs
    )

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug(
            "No occupancy sensors or absolute inputs found for %s — skipping "
            "binary_sensor entities",
            coordinator.data.label,
        )


class ZenOccupancySensor(CoordinatorEntity[ZenControlCoordinator], BinarySensorEntity):
    """Binary sensor representing one DALI occupancy sensor instance."""

    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        sensor: OccupancySensorInfo,
    ) -> None:
        super().__init__(coordinator)
        self._cd_address = sensor.cd_address
        self._instance_number = sensor.instance_number
        self._attr_unique_id = (
            f"{entry.entry_id}_{UID_OCCUPANCY}_{sensor.cd_address}_{sensor.instance_number}"
        )
        self._attr_name = sensor.label
        self._attr_device_info = coordinator.device_info
        self._attr_extra_state_attributes = {
            "dali_cd_address": sensor.cd_address,       # TPI address (64-127)
            "cd_index": sensor.cd_address - 64,         # CD index (0-63)
            "instance": sensor.instance_number,
            "hold_time_s": sensor.hold_time_s,
        }

    @property
    def is_on(self) -> bool:
        """Return True when the sensor reports occupancy."""
        return self.coordinator.data.sensor_occupancy.get(
            (self._cd_address, self._instance_number), False
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class ZenAbsoluteInputBinarySensor(
    CoordinatorEntity[ZenControlCoordinator], BinarySensorEntity
):
    """Binary sensor representing one DALI absolute-input instance.

    Absolute inputs report a value; here they are treated as an on/off switch
    input — on when the reported value is non-zero. The raw value is exposed as
    an attribute. This is a read-only input: Home Assistant cannot drive it.
    """

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        absinput: AbsoluteInputInfo,
    ) -> None:
        super().__init__(coordinator)
        self._cd_address = absinput.cd_address
        self._instance_number = absinput.instance_number
        self._attr_name = absinput.label
        self._attr_unique_id = (
            f"{entry.entry_id}_{UID_ABSOLUTE}_{absinput.cd_address}_{absinput.instance_number}"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def _raw_value(self) -> int | None:
        return self.coordinator.data.absolute_values.get(
            (self._cd_address, self._instance_number)
        )

    @property
    def is_on(self) -> bool:
        """On when the input reports a non-zero value."""
        value = self._raw_value
        return value is not None and value != 0

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "dali_cd_address": self._cd_address,        # TPI address (64-127)
            "cd_index": self._cd_address - 64,          # CD index (0-63)
            "instance": self._instance_number,
            "raw_value": self._raw_value,               # last reported 16-bit value
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
