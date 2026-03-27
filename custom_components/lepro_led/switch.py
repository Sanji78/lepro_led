"""Switch platform for Lepro LED power-only controls."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class LeproPowerSwitch(SwitchEntity):
    """Power-only switch for a Lepro device."""

    def __init__(self, light):
        self._light = light
        self._attr_has_entity_name = True
        self._attr_translation_key = "power"
        self._attr_unique_id = f"{light._did}_power"
        self._attr_device_info = getattr(light, "_attr_device_info", None)

    @property
    def is_on(self) -> bool | None:
        return bool(getattr(self._light, "_is_on", False))

    @property
    def available(self) -> bool:
        return True

    async def async_turn_on(self, **kwargs) -> None:
        self._light._is_on = True
        await self._light._send_mqtt_command({"d1": 1})
        self.async_write_ha_state()
        try:
            self._light.async_write_ha_state()
        except Exception:
            pass

    async def async_turn_off(self, **kwargs) -> None:
        self._light._is_on = False
        await self._light._send_mqtt_command({"d1": 0})
        self.async_write_ha_state()
        try:
            self._light.async_write_ha_state()
        except Exception:
            pass


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up power-only switch entities for Lepro devices."""
    attempts = 6
    for _ in range(attempts):
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if data and "entities" in data:
            break
        await asyncio.sleep(0.5)

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        _LOGGER.error(
            "Lepro LED: no data available in hass.data for entry %s - switch platform setup aborted",
            entry.entry_id,
        )
        return

    lights = data.get("entities", [])
    if not lights:
        _LOGGER.warning(
            "Lepro LED: no lights found for entry %s - no switch entities created",
            entry.entry_id,
        )
        return

    switches = []
    for light in lights:
        if not hasattr(light, "_did"):
            continue
        switches.append(LeproPowerSwitch(light))

    if switches:
        async_add_entities(switches)
