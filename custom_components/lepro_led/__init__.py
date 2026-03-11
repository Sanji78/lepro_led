from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv
import voluptuous as vol
import json
import random
import time
from .const import DOMAIN

# Config entry only (no YAML)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SERVICE_SEND_DEBUG_COMMAND = "send_debug_command"
SERVICE_REQUEST_DEBUG_STATE = "request_debug_state"
SERVICE_SCHEMA_SEND_DEBUG_COMMAND = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("payload"): dict,
        vol.Optional("entry_id"): cv.string,
    }
)
SERVICE_SCHEMA_REQUEST_DEBUG_STATE = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("entry_id"): cv.string,
        vol.Optional(
            "keys",
            default=["d1", "d2", "d3", "d4", "d5", "d30", "d50", "d52", "d60", "online"],
        ): [cv.string],
    }
)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up Lepro LED integration (not via YAML)."""
    def _resolve_entry_data(entry_id: str | None, device_id: str):
        entry_data = None
        if entry_id:
            entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
        else:
            for candidate in hass.data.get(DOMAIN, {}).values():
                entities = candidate.get("entities", [])
                if any(getattr(entity, "_did", None) == device_id for entity in entities):
                    entry_data = candidate
                    break
        return entry_data

    async def _async_send_debug_command(service_call):
        """Send a raw MQTT command to a Lepro device for protocol investigation."""
        entry_id = service_call.data.get("entry_id")
        device_id = str(service_call.data["device_id"])
        payload = service_call.data["payload"]

        entry_data = _resolve_entry_data(entry_id, device_id)
        if not entry_data:
            raise vol.Invalid(f"Could not find Lepro entry for device_id={device_id}")

        mqtt_client = entry_data.get("mqtt_client")
        if mqtt_client is None:
            raise vol.Invalid(f"No MQTT client available for device_id={device_id}")

        topic = f"le/{device_id}/prp/set"
        full_payload = {
            "id": random.randint(0, 1000000000),
            "t": int(time.time()),
            "d": payload,
        }
        await mqtt_client.publish(topic, json.dumps(full_payload))

    async def _async_request_debug_state(service_call):
        """Request raw MQTT state fields from a Lepro device."""
        entry_id = service_call.data.get("entry_id")
        device_id = str(service_call.data["device_id"])
        keys = service_call.data["keys"]

        entry_data = _resolve_entry_data(entry_id, device_id)
        if not entry_data:
            raise vol.Invalid(f"Could not find Lepro entry for device_id={device_id}")

        mqtt_client = entry_data.get("mqtt_client")
        if mqtt_client is None:
            raise vol.Invalid(f"No MQTT client available for device_id={device_id}")

        topic = f"le/{device_id}/prp/get"
        payload = json.dumps({"d": keys})
        await mqtt_client.publish(topic, payload)

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_DEBUG_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_DEBUG_COMMAND,
            _async_send_debug_command,
            schema=SERVICE_SCHEMA_SEND_DEBUG_COMMAND,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_REQUEST_DEBUG_STATE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REQUEST_DEBUG_STATE,
            _async_request_debug_state,
            schema=SERVICE_SCHEMA_REQUEST_DEBUG_STATE,
        )
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lepro LED from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # THIS is the correct version
    await hass.config_entries.async_forward_entry_setups(entry, ["light", "number"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["light", "number"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SEND_DEBUG_COMMAND)
            hass.services.async_remove(DOMAIN, SERVICE_REQUEST_DEBUG_STATE)
    return unload_ok
