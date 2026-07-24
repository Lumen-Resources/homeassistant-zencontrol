"""zencontrol Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_EVENT_PORT,
    CONF_USE_MULTICAST,
    DATA_COORDINATOR,
    DATA_EVENT_LISTENER,
    DEFAULT_EVENT_PORT,
    DEFAULT_USE_MULTICAST,
    DOMAIN,
    get_entry_config,
)
from .coordinator import ZenControlCoordinator
from .tpi import EventListener

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a zencontrol controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # ----------------------------------------------------------------
    # Shared event listener (one per HA instance, shared across entries)
    # ----------------------------------------------------------------
    cfg = get_entry_config(entry)

    if DATA_EVENT_LISTENER not in hass.data[DOMAIN]:
        event_port: int = cfg.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT)
        use_multicast: bool = cfg.get(CONF_USE_MULTICAST, DEFAULT_USE_MULTICAST)
        listener = EventListener(port=event_port, use_multicast=use_multicast)
        try:
            await listener.start()
        except OSError as exc:
            raise ConfigEntryNotReady(
                f"Cannot open event listener on port {event_port}: {exc}"
            ) from exc
        hass.data[DOMAIN][DATA_EVENT_LISTENER] = listener
        _LOGGER.debug("Shared event listener started on port %d", event_port)

    listener: EventListener = hass.data[DOMAIN][DATA_EVENT_LISTENER]

    # ----------------------------------------------------------------
    # Per-controller coordinator
    # ----------------------------------------------------------------
    coordinator = ZenControlCoordinator(hass, entry.entry_id, cfg)

    # First refresh — runs discovery and polls initial state
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        raise

    # Register with the shared event listener and enable events on the controller
    await coordinator.setup_events(listener)

    hass.data[DOMAIN][entry.entry_id] = {DATA_COORDINATOR: coordinator}

    # Register the controller device before forwarding to entity platforms —
    # per-fixture and per-keypad sub-devices reference it as via_device, and
    # the parent must already exist when they are created (hard error in
    # future HA releases otherwise).
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **coordinator.device_info,
    )

    # Forward to entity platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Re-run setup if options change (user added/removed short addresses)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry option updates (e.g. added short addresses)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a zencontrol config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, {})
        coordinator: ZenControlCoordinator | None = entry_data.get(DATA_COORDINATOR)
        if coordinator:
            await coordinator.async_disconnect()

        # Stop shared listener only when the last entry is removed
        remaining = [
            k for k in hass.data[DOMAIN]
            if k not in (DATA_EVENT_LISTENER,)
        ]
        if not remaining:
            listener: EventListener | None = hass.data[DOMAIN].pop(DATA_EVENT_LISTENER, None)
            if listener:
                await listener.stop()
                _LOGGER.debug("Shared event listener stopped")

    return unload_ok
