"""Device triggers for zencontrol push buttons.

Exposes each discovered push button as "press" and "hold" device triggers so
they can be selected by name in the Home Assistant automation UI. Triggers
share the same dispatcher signal that drives the button `event` entities, so
there is a single event source.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    BUTTON_EVENT_HOLD,
    BUTTON_EVENT_PRESS,
    DATA_COORDINATOR,
    DOMAIN,
    SIGNAL_BUTTON_EVENT,
)
from .coordinator import ZenControlCoordinator

CONF_SUBTYPE = "subtype"
CONF_CD_ADDRESS = "cd_address"
CONF_INSTANCE = "instance"

TRIGGER_TYPES = {BUTTON_EVENT_PRESS, BUTTON_EVENT_HOLD}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_SUBTYPE): str,
        vol.Required(CONF_CD_ADDRESS): int,
        vol.Required(CONF_INSTANCE): int,
    }
)


def _coordinator_for_device(
    hass: HomeAssistant, device_id: str
) -> tuple[str | None, ZenControlCoordinator | None]:
    """Resolve the config entry id and coordinator backing an HA device."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return None, None
    for entry_id in device.config_entries:
        data = hass.data.get(DOMAIN, {}).get(entry_id)
        if data and DATA_COORDINATOR in data:
            return entry_id, data[DATA_COORDINATOR]
    return None, None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str | int]]:
    """List press/hold triggers for every push button on this controller."""
    _entry_id, coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        return []

    triggers: list[dict[str, str | int]] = []
    for button in coordinator.data.buttons:
        for trigger_type in (BUTTON_EVENT_PRESS, BUTTON_EVENT_HOLD):
            triggers.append(
                {
                    CONF_PLATFORM: "device",
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_TYPE: trigger_type,
                    CONF_SUBTYPE: button.label,
                    CONF_CD_ADDRESS: button.cd_address,
                    CONF_INSTANCE: button.instance_number,
                }
            )
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a device trigger by subscribing to the button dispatcher signal."""
    entry_id, _coordinator = _coordinator_for_device(hass, config[CONF_DEVICE_ID])
    if entry_id is None:
        return lambda: None

    trigger_type = config[CONF_TYPE]
    cd_address = config[CONF_CD_ADDRESS]
    instance = config[CONF_INSTANCE]
    job = HassJob(action)
    trigger_data = trigger_info["trigger_data"]

    @callback
    def _handle(cd: int, inst: int, event_type: str) -> None:
        if cd == cd_address and inst == instance and event_type == trigger_type:
            hass.async_run_hass_job(
                job,
                {
                    "trigger": {
                        **trigger_data,
                        CONF_PLATFORM: "device",
                        CONF_DOMAIN: DOMAIN,
                        CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                        CONF_TYPE: trigger_type,
                        CONF_SUBTYPE: config.get(CONF_SUBTYPE),
                        CONF_CD_ADDRESS: cd,
                        CONF_INSTANCE: inst,
                    }
                },
            )

    return async_dispatcher_connect(
        hass, SIGNAL_BUTTON_EVENT.format(entry_id), _handle
    )
