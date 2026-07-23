"""zencontrol profile selector entity."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN, UID_PROFILE
from .coordinator import ZenControlCoordinator
from .tpi import PROFILE_SCHEDULE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a profile select entity for the controller."""
    coordinator: ZenControlCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    if not coordinator.data.profile_info.profiles:
        _LOGGER.debug("No profiles found for %s — skipping select entity", coordinator.data.label)
        return

    async_add_entities([ZenProfileSelect(coordinator, entry)])


class ZenProfileSelect(CoordinatorEntity[ZenControlCoordinator], SelectEntity):
    """Allows the user to select the active controller profile."""

    _attr_should_poll = False
    _attr_icon = "mdi:tune"

    def __init__(self, coordinator: ZenControlCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{UID_PROFILE}"
        self._attr_name = f"{coordinator.data.label} Profile"
        self._attr_device_info = coordinator.device_info
        self._build_options()

    # ------------------------------------------------------------------
    # Options list
    # ------------------------------------------------------------------

    def _build_options(self) -> None:
        """Build option lists from available profiles."""
        self._profile_by_label: dict[str, int] = {}
        self._label_by_profile: dict[int, str] = {}
        options: list[str] = []
        for p in self.coordinator.data.profile_info.profiles:
            label = p.label or f"Profile {p.number}"
            self._profile_by_label[label] = p.number
            self._label_by_profile[p.number] = label
            options.append(label)
        self._attr_options = options

    # ------------------------------------------------------------------
    # Current value
    # ------------------------------------------------------------------

    @property
    def current_option(self) -> str | None:
        return self._label_by_profile.get(self.coordinator.data.current_profile)

    # ------------------------------------------------------------------
    # Select action
    # ------------------------------------------------------------------

    async def async_select_option(self, option: str) -> None:
        profile_id = self._profile_by_label.get(option)
        if profile_id is None:
            _LOGGER.warning("Unknown profile option selected: %s", option)
            return
        ok = await self.coordinator.commands.change_profile(profile_id)
        if ok:
            # Optimistic update — don't wait for PROFILE_CHANGED push event
            self.coordinator.data.current_profile = profile_id
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Profile change to '%s' (id=%d) was refused", option, profile_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._build_options()
        self.async_write_ha_state()
