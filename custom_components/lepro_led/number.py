"""Number platform for Lepro LED speed selectors."""

from __future__ import annotations
import asyncio
import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.number import NumberEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class LeproSpeedNumber(NumberEntity):
    """Number entity to control the 'speed' parameter for a Lepro LED light."""

    def __init__(self, light):
        self._light = light
        self._attr_has_entity_name = True
        self._attr_translation_key = "speed"
        self._attr_unique_id = f"{light._did}_speed"
        self._attr_device_info = getattr(light, "_attr_device_info", None)
        
        # native (0-100) slider configuration
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_mode = "slider"

    @property
    def native_value(self) -> float:
        """Return the current speed value (0-100)."""
        # reflect the in-memory light speed; when light receives MQTT it updates this value
        return float(getattr(self._light, "_speed", 50))

    async def async_set_native_value(self, value: float) -> None:
        """Set the speed on the light and send the corresponding command."""
        new_speed = max(0, min(100, int(round(value))))
        self._light._speed = new_speed

        try:
            if getattr(self._light, "_effect", None) in getattr(self._light, "SPECIAL_EFFECTS", []):
                await self._light._send_special_effect_command(self._light._effect)
            else:
                await self._light._send_effect_command()
                
        except Exception as e:
            _LOGGER.error("Error applying speed change for %s: %s", self._light.name, e)

        # update HA state for both number and related light entity
        self.async_write_ha_state()
        try:
            self._light.async_write_ha_state()
        except Exception:
            # ignore if light not yet added
            pass

class LeproSensitivityNumber(NumberEntity):
    """Number entity to control the 'sensitivity' parameter (for music/special effects)."""

    def __init__(self, light):
        self._light = light
        self._attr_has_entity_name = True
        self._attr_translation_key = "sensitivity"
        self._attr_unique_id = f"{light._did}_sensitivity"
        self._attr_device_info = getattr(light, "_attr_device_info", None)

        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_mode = "slider"

    @property
    def native_value(self) -> float:
        return float(getattr(self._light, "_sensitivity", 50))

    async def async_set_native_value(self, value: float) -> None:
        new_sens = max(0, min(100, int(round(value))))
        self._light._sensitivity = new_sens
        try:
            if getattr(self._light, "_effect", None) in getattr(self._light, "SPECIAL_EFFECTS", []):
                await self._light._send_special_effect_command(self._light._effect)
            else:
                await self._light._send_effect_command()
                
        except Exception as e:
            _LOGGER.error("Error applying sensitivity change for %s: %s", self._light.name, e)

        self.async_write_ha_state()
        try:
            self._light.async_write_ha_state()
        except Exception:
            pass
            
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up number entities for Lepro LED speeds."""
    # Wait a short while for light platform to populate hass.data, but keep attempts limited.
    attempts = 100
    for attempt in range(attempts):
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if data and "entities" in data:
            break
        await asyncio.sleep(0.5)

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        _LOGGER.error("Lepro LED: no data available in hass.data for entry %s — number platform setup aborted", entry.entry_id)
        return

    lights = data.get("entities", [])
    if not lights:
        _LOGGER.warning("Lepro LED: no lights found for entry %s — no number entities created", entry.entry_id)
        return

    numbers = []
    for light in lights:
        
        if not hasattr(light, "_did"):
            _LOGGER.debug("Skipping number for segment or non-parent entity: %s", getattr(light, "name", "unknown"))
            continue
            
        try:
            numbers.append(LeproSpeedNumber(light))
            numbers.append(LeproSensitivityNumber(light))
        except Exception as e:
            _LOGGER.error("Failed to create speed number for %s: %s", getattr(light, "name", "unknown"), e)

    if numbers:
        # NEW: register numbers so light.py can refresh their state on MQTT updates
        store = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
        numbers_map = store.setdefault("numbers", {})  # { did: [NumberEntity, ...] }

        for num in numbers:
            did = getattr(num._light, "_did", None)
            if did:
                numbers_map.setdefault(did, []).append(num)

        async_add_entities(numbers)
