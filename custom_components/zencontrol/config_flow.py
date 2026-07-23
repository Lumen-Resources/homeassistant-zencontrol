"""Config flow for the zencontrol integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_EVENT_PORT,
    CONF_HOST,
    CONF_PORT,
    CONF_SCENE_ADDRESS,
    CONF_SCENE_NAME,
    CONF_SCENE_NUMBER,
    CONF_SCENES,
    CONF_SYSTEM_VARIABLES,
    CONF_SYSVAR_NAME,
    CONF_SYSVAR_NUMBER,
    CONF_USE_MULTICAST,
    DATA_COORDINATOR,
    DEFAULT_EVENT_PORT,
    DEFAULT_PORT,
    DEFAULT_USE_MULTICAST,
    DOMAIN,
    get_entry_config,
)
from .tpi import DALI_GROUP_OFFSET, TpiClient, ZenCommands

_LOGGER = logging.getLogger(__name__)


class ZenControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup flow for a zencontrol controller."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._discovered_label: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 — ask for the controller IP address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = user_input.get(CONF_PORT, DEFAULT_PORT)

            label, error = await self._try_connect(self._host, self._port)
            if error:
                errors["base"] = error
            else:
                self._discovered_label = label or self._host
                await self.async_set_unique_id(f"zencontrol_{self._host}")
                self._abort_if_unique_id_configured()
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }),
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2 — confirm discovered controller and choose event transport."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_label,
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_EVENT_PORT: user_input.get(CONF_EVENT_PORT, DEFAULT_EVENT_PORT),
                    CONF_USE_MULTICAST: user_input.get(CONF_USE_MULTICAST, DEFAULT_USE_MULTICAST),
                    CONF_SCENES: [],
                },
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"label": self._discovered_label, "host": self._host},
            data_schema=vol.Schema({
                vol.Optional(CONF_USE_MULTICAST, default=DEFAULT_USE_MULTICAST): bool,
                vol.Optional(CONF_EVENT_PORT, default=DEFAULT_EVENT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1024, max=65535)
                ),
            }),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _try_connect(host: str, port: int) -> tuple[str, str | None]:
        client = TpiClient(host=host, port=port, timeout=5.0)
        try:
            await client.connect()
            cmds = ZenCommands(client)
            for _ in range(5):
                if await cmds.query_startup_complete():
                    break
                await asyncio.sleep(1)
            label = await cmds.query_controller_label()
            return label or host, None
        except asyncio.TimeoutError:
            return "", "cannot_connect"
        except OSError:
            return "", "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error connecting to %s:%d", host, port)
            return "", "unknown"
        finally:
            await client.disconnect()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ZenControlOptionsFlow:
        return ZenControlOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow — manage manually configured scenes
# ---------------------------------------------------------------------------

class ZenControlOptionsFlow(OptionsFlow):
    """Allow users to manage manually configured scenes."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        merged = get_entry_config(config_entry)
        self._scenes: list[dict] = list(merged.get(CONF_SCENES, []))
        self._sysvars: list[dict] = list(merged.get(CONF_SYSTEM_VARIABLES, []))

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_scene", "remove_scene",
                "add_system_variable", "remove_system_variable",
                "done",
            ],
        )

    # ------------------------------------------------------------------
    # Scene management
    # ------------------------------------------------------------------

    async def async_step_add_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a manually configured scene.

        The user specifies:
          - Target type: Group (0-15) or Short Address (0-63)
          - Target number
          - Scene number (0-12)
          - Optional display name (auto-fetched from controller if blank)
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            target_type = user_input["target_type"]
            target_number = user_input["target_number"]
            scene_number = user_input[CONF_SCENE_NUMBER]
            custom_name = user_input.get(CONF_SCENE_NAME, "").strip()

            # Convert to DALI address
            if target_type == "group":
                if target_number > 15:
                    errors["target_number"] = "invalid_group"
                else:
                    dali_address = target_number + DALI_GROUP_OFFSET
            else:
                if target_number > 63:
                    errors["target_number"] = "invalid_address"
                else:
                    dali_address = target_number

            if not errors:
                # Check for duplicate
                existing = any(
                    s[CONF_SCENE_ADDRESS] == dali_address
                    and s[CONF_SCENE_NUMBER] == scene_number
                    for s in self._scenes
                )
                if existing:
                    errors["base"] = "scene_exists"
                else:
                    # Auto-fetch label from controller if no name given
                    if not custom_name:
                        custom_name = await self._fetch_scene_label(
                            dali_address, target_type, target_number, scene_number
                        )

                    self._scenes.append({
                        CONF_SCENE_ADDRESS: dali_address,
                        CONF_SCENE_NUMBER: scene_number,
                        CONF_SCENE_NAME: custom_name,
                    })
                    return await self.async_step_init()

        return self.async_show_form(
            step_id="add_scene",
            data_schema=vol.Schema({
                vol.Required("target_type", default="group"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "group", "label": "Group (0–15)"},
                            {"value": "address", "label": "Short Address (0–63)"},
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required("target_number", default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=63)
                ),
                vol.Required(CONF_SCENE_NUMBER, default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=12)
                ),
                vol.Optional(CONF_SCENE_NAME, default=""): str,
            }),
            errors=errors,
        )

    async def async_step_remove_scene(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            idx = int(user_input["scene_index"])
            if 0 <= idx < len(self._scenes):
                self._scenes.pop(idx)
            return await self.async_step_init()

        if not self._scenes:
            return self.async_abort(reason="no_scenes")

        options = [
            {
                "value": str(i),
                "label": s.get(CONF_SCENE_NAME) or f"Scene {s[CONF_SCENE_NUMBER]} @ addr {s[CONF_SCENE_ADDRESS]}",
            }
            for i, s in enumerate(self._scenes)
        ]

        return self.async_show_form(
            step_id="remove_scene",
            data_schema=vol.Schema({
                vol.Required("scene_index"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                ),
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # System variable management
    # ------------------------------------------------------------------

    async def async_step_add_system_variable(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manually add a system variable to expose as a sensor.

        Useful on non-Pro controllers (which can't be auto-discovered by name)
        or to expose an unnamed variable.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            number = user_input[CONF_SYSVAR_NUMBER]
            name = user_input.get(CONF_SYSVAR_NAME, "").strip()
            if any(sv[CONF_SYSVAR_NUMBER] == number for sv in self._sysvars):
                errors["base"] = "sysvar_exists"
            else:
                self._sysvars.append({
                    CONF_SYSVAR_NUMBER: number,
                    CONF_SYSVAR_NAME: name or f"System Variable {number}",
                })
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_system_variable",
            data_schema=vol.Schema({
                vol.Required(CONF_SYSVAR_NUMBER, default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=147)
                ),
                vol.Optional(CONF_SYSVAR_NAME, default=""): str,
            }),
            errors=errors,
        )

    async def async_step_remove_system_variable(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            idx = int(user_input["sysvar_index"])
            if 0 <= idx < len(self._sysvars):
                self._sysvars.pop(idx)
            return await self.async_step_init()

        if not self._sysvars:
            return self.async_abort(reason="no_sysvars")

        options = [
            {
                "value": str(i),
                "label": s.get(CONF_SYSVAR_NAME) or f"System Variable {s[CONF_SYSVAR_NUMBER]}",
            }
            for i, s in enumerate(self._sysvars)
        ]

        return self.async_show_form(
            step_id="remove_system_variable",
            data_schema=vol.Schema({
                vol.Required("sysvar_index"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                ),
            }),
        )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        new_data = dict(self._entry.data)
        new_data[CONF_SCENES] = self._scenes
        new_data[CONF_SYSTEM_VARIABLES] = self._sysvars
        return self.async_create_entry(title="", data=new_data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetch_scene_label(
        self,
        dali_address: int,
        target_type: str,
        target_number: int,
        scene_number: int,
    ) -> str:
        """Try to fetch the scene label from the controller; fall back to a default."""
        coordinator_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        coordinator = coordinator_data.get(DATA_COORDINATOR)
        if coordinator:
            try:
                if target_type == "group":
                    label = await coordinator.commands.query_scene_label_for_group(
                        target_number, scene_number
                    )
                else:
                    # No per-address scene label query — use group label fallback
                    label = None
                if label:
                    return label
            except Exception:  # noqa: BLE001
                pass

        # Fallback name
        prefix = f"G{target_number}" if target_type == "group" else f"A{target_number}"
        return f"{prefix} Scene {scene_number}"
