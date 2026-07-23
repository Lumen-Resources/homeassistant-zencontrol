"""zencontrol switch entities — DALI relay devices at manually-added short addresses."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    UID_BUTTON_LED,
    UID_SHORT,
)
from .coordinator import ButtonInfo, DeviceState, ZenControlCoordinator
from .tpi import ARC_LEVEL_MAX, ARC_LEVEL_OFF, DaliCgTypeMask

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create switch entities for relay short addresses and push-button LEDs."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list[SwitchEntity] = []

    # Relay-type short addresses
    for addr in coordinator.data.short_addresses:
        cg_type = coordinator.data.short_address_types.get(addr, DaliCgTypeMask(0))
        if DaliCgTypeMask.RELAY in cg_type:
            entities.append(ZenRelaySwitch(coordinator, entry, addr))

    # Push-button LEDs (one per button). The protocol has no "has LED" query,
    # so these are disabled by default unless a definite LED state was read at
    # discovery — the user enables the ones their keypads actually have.
    for button in coordinator.data.buttons:
        entities.append(ZenButtonLedSwitch(coordinator, entry, button))

    async_add_entities(entities)


class ZenRelaySwitch(CoordinatorEntity[ZenControlCoordinator], SwitchEntity):
    """Switch entity for a DALI relay at a fixed short address."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        address: int,
    ) -> None:
        super().__init__(coordinator)
        self._address = address
        label = coordinator.data.short_address_labels.get(address, f"Relay {address}")
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_{UID_SHORT}_relay_{address}"
        self._attr_device_info = coordinator.device_info
        self._attr_extra_state_attributes = {"dali_address": address}

    @property
    def _device_state(self) -> DeviceState:
        return self.coordinator.get_device_state(self._address)

    @property
    def is_on(self) -> bool:
        return self._device_state.arc_level != ARC_LEVEL_OFF

    async def async_turn_on(self, **kwargs) -> None:  # type: ignore[override]
        await self.coordinator.commands.recall_max(self._address)
        # Optimistic update — don't wait for push event
        state = self.coordinator.data.device_states.get(self._address)
        if state is not None:
            state.arc_level = ARC_LEVEL_MAX
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:  # type: ignore[override]
        await self.coordinator.commands.set_off(self._address)
        # Optimistic update — don't wait for push event
        state = self.coordinator.data.device_states.get(self._address)
        if state is not None:
            state.arc_level = ARC_LEVEL_OFF
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class ZenButtonLedSwitch(CoordinatorEntity[ZenControlCoordinator], SwitchEntity):
    """Switch controlling the indicator LED of a DALI push button.

    The controller reports no "has LED" capability, so state is optimistic:
    there is no push event for LED changes. The last-known state is read once
    at discovery.
    """

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        button: ButtonInfo,
    ) -> None:
        super().__init__(coordinator)
        self._cd_address = button.cd_address
        self._instance_number = button.instance_number
        self._attr_name = f"{button.label} LED"
        self._attr_unique_id = (
            f"{entry.entry_id}_{UID_BUTTON_LED}_{button.cd_address}_{button.instance_number}"
        )
        self._attr_device_info = coordinator.device_info
        # Enable by default only when a definite LED state was read at discovery.
        self._attr_entity_registry_enabled_default = button.has_led
        self._attr_extra_state_attributes = {
            "dali_cd_address": button.cd_address,       # TPI address (64-127)
            "cd_index": button.cd_address - 64,         # CD index (0-63)
            "instance": button.instance_number,
        }

    @property
    def _key(self) -> tuple[int, int]:
        return (self._cd_address, self._instance_number)

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.button_led_state.get(self._key, False)

    async def async_turn_on(self, **kwargs) -> None:  # type: ignore[override]
        ok = await self.coordinator.commands.override_button_led(
            self._cd_address, self._instance_number, True
        )
        if ok:
            self.coordinator.data.button_led_state[self._key] = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:  # type: ignore[override]
        ok = await self.coordinator.commands.override_button_led(
            self._cd_address, self._instance_number, False
        )
        if ok:
            self.coordinator.data.button_led_state[self._key] = False
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
