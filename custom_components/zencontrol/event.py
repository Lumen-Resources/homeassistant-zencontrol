"""zencontrol push-button event entities."""
from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BUTTON_EVENT_HOLD,
    BUTTON_EVENT_PRESS,
    DATA_COORDINATOR,
    DOMAIN,
    SIGNAL_BUTTON_EVENT,
    UID_BUTTON,
)
from .coordinator import ButtonInfo, ZenControlCoordinator

_LOGGER = logging.getLogger(__name__)

BUTTON_EVENT_TYPES = [BUTTON_EVENT_PRESS, BUTTON_EVENT_HOLD]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create an event entity for each discovered push button."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities = [
        ZenButtonEvent(coordinator, entry, button)
        for button in coordinator.data.buttons
    ]

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug(
            "No push buttons found for %s — skipping event entities",
            coordinator.data.label,
        )


class ZenButtonEvent(EventEntity):
    """Event entity representing one DALI push-button instance.

    Button presses/holds are transient events delivered via a dispatcher
    signal from the coordinator. The entity's state is the timestamp of the
    most recent event, with the event type ("press"/"hold") as an attribute.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = BUTTON_EVENT_TYPES

    def __init__(
        self,
        coordinator: ZenControlCoordinator,
        entry: ConfigEntry,
        button: ButtonInfo,
    ) -> None:
        self._entry_id = entry.entry_id
        self._cd_address = button.cd_address
        self._instance_number = button.instance_number
        self._attr_name = button.label
        self._attr_unique_id = (
            f"{entry.entry_id}_{UID_BUTTON}_{button.cd_address}_{button.instance_number}"
        )
        self._attr_device_info = coordinator.device_info_for_cd(button.cd_address)
        self._attr_extra_state_attributes = {
            "dali_cd_address": button.cd_address,       # TPI address (64-127)
            "cd_index": button.cd_address - 64,         # CD index (0-63)
            "instance": button.instance_number,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to the coordinator's button-event dispatcher signal."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_BUTTON_EVENT.format(self._entry_id),
                self._handle_button_event,
            )
        )

    @callback
    def _handle_button_event(
        self, cd_address: int, instance_number: int, event_type: str
    ) -> None:
        """Fire the event if it targets this button instance."""
        if cd_address != self._cd_address or instance_number != self._instance_number:
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()
