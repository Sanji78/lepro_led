import asyncio
import aiohttp
import logging
import time
import json
import random
import ssl
import os
import hashlib
import re
import numpy as np
import colorsys
from .const import DOMAIN, REGIONS, LOGIN_PATH, FAMILY_LIST_PATH, USER_PROFILE_PATH, DEVICE_LIST_PATH, SWITCH_API_PATH
from aiomqtt import Client, MqttError
import aiofiles
from homeassistant.core import callback

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_EFFECT,
    ATTR_RGBW_COLOR,
    LightEntity,
    ColorMode,
    LightEntityFeature,
)

from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

class MQTTClientWrapper:
    def __init__(self, hass, host, port, ssl_context, client_id):
        self.hass = hass
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.client_id = client_id
        self.client = None
        self._message_callback = None
        self._loop_task = None
        self._pending_subscriptions = []
        self._pending_messages = []

    async def _connect_and_run(self):
        try:
            async with Client(
                hostname=self.host,
                port=self.port,
                identifier=self.client_id,
                tls_context=self.ssl_context,
                clean_session=True
            ) as client:
                self.client = client
                
                # Process pending subscriptions
                for topic in self._pending_subscriptions:
                    await client.subscribe(topic)
                self._pending_subscriptions = []
                
                # Process pending messages
                for topic, payload in self._pending_messages:
                    await client.publish(topic, payload)
                self._pending_messages = []
                
                # Start message loop
                async for message in client.messages:
                    if self._message_callback:
                        await self._message_callback(message)
        except MqttError as e:
            _LOGGER.error("MQTT error: %s", e)
        finally:
            self.client = None

    async def connect(self):
        if self._loop_task and not self._loop_task.done():
            return
            
        self._pending_subscriptions = []
        self._pending_messages = []
        self._loop_task = asyncio.create_task(self._connect_and_run())

    async def subscribe(self, topic):
        if self.client:
            await self.client.subscribe(topic)
        else:
            self._pending_subscriptions.append(topic)
            if not self._loop_task or self._loop_task.done():
                await self.connect()

    async def publish(self, topic, payload):
        if self.client:
            await self.client.publish(topic, payload)
        else:
            self._pending_messages.append((topic, payload))
            if not self._loop_task or self._loop_task.done():
                await self.connect()

    def set_message_callback(self, callback):
        self._message_callback = callback

    async def disconnect(self):
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

async def async_login(session, account, password, mac, login_url, api_host, language="en", fcm_token=""):
    """Perform login and return bearer token."""
    timestamp = str(int(time.time()))
    payload = {
        "platform": "2",
        "account": account,
        "password": password,
        "mac": mac,
        "timestamp": timestamp,
        "language": language,
        "fcmToken": fcm_token,
    }
    headers = {
        "Content-Type": "application/json",
        "App-Version": "1.0.9.202",
        "Device-Model": "custom_integration",
        "Device-System": "custom",
        "GMT": "+0",
        "Host": api_host,
        "Language": language,
        "Platform": "2",
        "Screen-Size": "1536*2048",
        "Slanguage": language,
        "Timestamp": timestamp,
        "User-Agent": "LE/1.0.9.202 (Custom Integration)",
    }

    async with session.post(login_url, json=payload, headers=headers) as resp:
        if resp.status != 200:
            _LOGGER.error("Login failed with status %s", resp.status)
            return None
        data = await resp.json()
        if data.get("code") != 0:
            _LOGGER.error("Login failed with message: %s", data.get("msg"))
            return None
        token = data.get("data", {}).get("token")
        return token


class LeproLedLight(LightEntity):
    B1_STATIC_STATE_FALLBACK = {
        "d2": 0,
        "d3": 1000,
        "d4": 500,
        "d30": "00002151",
    }
    B1_RGB_STATE_FALLBACK = {
        "d2": 1,
        "d3": 1000,
        "d4": 0,
        "d5": "000003E803E8",
    }
    # Effect constants
    EFFECT_NONE = "none"
    EFFECT_SOLID = "solid"
    EFFECT_BREATH = "breath"
    EFFECT_GRADIENT = "gradient"
    EFFECT_CLOCKWISE = "clockwise"
    EFFECT_COUNTERCLOCKWISE = "counterclockwise"
    EFFECT_CIRCULAR = "circular"
    EFFECT_FLASH = "flash"
    EFFECT_WAVE1 = "wave_1"
    EFFECT_WAVE2 = "wave_2"
    EFFECT_WAVE3 = "wave_3"
    EFFECT_WAVE4 = "wave_4"
    EFFECT_LASER1 = "laser_1"
    EFFECT_LASER2 = "laser_2"
    EFFECT_LASER3 = "laser_3"
    EFFECT_LASER4 = "laser_4"

    # Mapping effect -> d60 7-char prefix (first 7 digits)
    SPECIAL_EFFECT_TO_D60_PREFIX = {
        EFFECT_FLASH: "2000064",
        EFFECT_WAVE1: "2010064",
        EFFECT_WAVE2: "2020064",
        EFFECT_WAVE3: "2030064",
        EFFECT_WAVE4: "2040064",
        EFFECT_LASER1: "2050064",
        EFFECT_LASER2: "2060064",
        EFFECT_LASER3: "2070064",
        EFFECT_LASER4: "2080064",
    }

    # Set of special effects for quick checks
    SPECIAL_EFFECTS = set(SPECIAL_EFFECT_TO_D60_PREFIX.keys())
    
    def __init__(self, device, mqtt_client, entry_id):
        self._device = device
        # self._attr_name = device["name"]
        self._attr_unique_id = str(device["did"])
        self._fid = device["fid"]
        self._mqtt_client = mqtt_client
        self._entry_id = entry_id
        self._did = str(device["did"])
        self._attr_has_entity_name = True
        self._attr_translation_key = "strip"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._did)},
            "name": device["name"],
            "manufacturer": "Lepro",
            "model": device.get("series", "Lepro LED"),
        }        
        # State variables
        self._is_on = bool(device.get("switch", 0))
        self._mode = device.get("d2", 2)  # Default to static mode
        self._effect = self.EFFECT_SOLID
        self._speed = 50  # Default speed (0-100)
        self._normalizing_effect = False
        self._b1_static_state = {}
        self._b1_rgb_state = {}
        # Keep B1-specific default
        if self.is_b1_model:
            self._effect = self.EFFECT_NONE
    
        # store 25 segments internally; main light mirrors segment 0
        self._segment_colors = [(255, 255, 255)] * 25  # Default all white
        self._sensitivity = 50  # For music mode
        
        # Initialize from device data
        if "d50" in device:
            self._parse_d50(device["d50"])
        if "d52" in device:
            self._brightness = self._map_device_brightness(device["d52"])
        else:
            self._brightness = 255
        if "d60" in device:
            self._sensitivity = self._parse_d60(device["d60"])
        self._update_b1_static_state(device)
        
        # Entity attributes
        self._attr_supported_features = LightEntityFeature.EFFECT
        self._attr_color_mode = ColorMode.RGB
        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_effect_list = [
            self.EFFECT_NONE,
            self.EFFECT_BREATH,
            self.EFFECT_GRADIENT,
            self.EFFECT_CLOCKWISE,
            self.EFFECT_COUNTERCLOCKWISE,
            self.EFFECT_CIRCULAR,
            self.EFFECT_FLASH,
            self.EFFECT_WAVE1,
            self.EFFECT_WAVE2,
            self.EFFECT_WAVE3,
            self.EFFECT_WAVE4,
            self.EFFECT_LASER1,
            self.EFFECT_LASER2,
            self.EFFECT_LASER3,
            self.EFFECT_LASER4,
        ]
        # main light color is the first segment color
        self._attr_rgb_color = self._segment_colors[0]  # First segment as primary color

    @property
    def is_b1_model(self):
        """Return True when the device model/series indicates a B1 bulb."""
        model = str(self._attr_device_info.get("model", "")).upper()
        return "B1" in model

    def _should_skip_d50_for_static_mode(self):
        """Use a reduced payload for B1 bulbs to test whether d50 causes flashing."""
        return self.is_b1_model and self._effect in (self.EFFECT_NONE, self.EFFECT_SOLID)

    def _update_b1_static_state(self, source: dict):
        """Store the latest known-good B1 static-mode fields."""
        if not self.is_b1_model:
            return

        static_state = {
            key: source[key]
            for key in ["d2", "d3", "d4", "d30"]
            if key in source
        }
        if not static_state:
            return

        if static_state.get("d2", self._b1_static_state.get("d2")) == 0:
            self._b1_static_state.update(static_state)

    def _get_b1_static_payload(self, brightness=None):
        """Return the best-known payload for B1 static mode."""
        payload = dict(self.B1_STATIC_STATE_FALLBACK)
        payload.update(self._b1_static_state)
        if brightness is not None:
            payload["d3"] = int(round(max(0.0, min(1.0, brightness / 255)) * 1000))
            payload["d4"] = int(payload.get("d4", self.B1_STATIC_STATE_FALLBACK["d4"]))
        payload.pop("d5", None)
        payload.pop("d30", None)
        return payload

    def _update_b1_rgb_state(self, source: dict):
        """Store the latest known-good B1 RGB-mode fields."""
        if not self.is_b1_model:
            return

        rgb_state = {
            key: source[key]
            for key in ["d2", "d3", "d4", "d5"]
            if key in source
        }
        if not rgb_state:
            return

        if rgb_state.get("d2", self._b1_rgb_state.get("d2")) == 1:
            self._b1_rgb_state.update(rgb_state)

    def _build_b1_rgb_payload(self, rgb_color, brightness):
        """Build a B1 RGB payload that matches the official app format."""
        payload = {"d2": 1}

        r, g, b = [int(max(0, min(255, c))) for c in rgb_color]
        hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        hue_deg = int(round((hue * 360) % 360))
        brightness_scale = max(0.0, min(1.0, brightness / 255 if brightness is not None else 1.0))
        sat_hex = "03E8"
        val_int = int(round(val * brightness_scale * 1000))
        val_hex = f"{val_int:04X}"
        payload["d5"] = f"{hue_deg:04X}{sat_hex}{val_hex}"
        return payload

    def _is_b1_white_like(self, rgb_color):
        """Return True when a B1 RGB request is effectively white/gray."""
        r, g, b = [int(max(0, min(255, c))) for c in rgb_color]
        _, sat, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        return sat <= 0.05
            
    def _map_device_brightness(self, device_brightness):
        """Map device brightness (100-1000) to HA brightness (0-255)"""
        _LOGGER.info("device_brightness: %s", str(device_brightness))
        # return int((device_brightness - 100) * 255 / 900)
        return int((device_brightness - 0) * 255 / 1000)
    
    def _map_ha_brightness(self, ha_brightness):
        """Map HA brightness (0-255) to device brightness (100-1000)"""
        # return 100 + int(ha_brightness * 900 / 255)
        return 0 + int(ha_brightness * 1000 / 255)
    
    def _parse_d60(self, d60_str):
        """
        Parse d60 string for special-effects and sensitivity.
        Expected d60: <7-char-effect-prefix><2-hex-sensitivity><0000>
        Example: "2000064320000" -> prefix "2000064", sens_hex "32" -> sens_percent ~51

        Returns: (sensitivity_percent_int, effect_name_or_None)
        """
        try:
            if not d60_str or len(d60_str) < 9:
                return 50, None

            prefix = d60_str[0:7]
            sens_hex = d60_str[7:9]

            # parse hex sensitivity (0x00 .. 0x63)
            try:
                sens_val = int(sens_hex, 16)
            except Exception:
                sens_val = 0x00

            # Map sens_val (0..0x63) -> percent 0..100
            MAX_HEX = 0x63  # 99 decimal; spec: 100 -> 0x63
            sens_percent = round(sens_val * 100 / MAX_HEX) if MAX_HEX > 0 else 0
            # clamp
            sens_percent = max(0, min(100, int(sens_percent)))

            # map prefix to effect
            prefix_map = {v: k for k, v in self.SPECIAL_EFFECT_TO_D60_PREFIX.items()}
            effect = prefix_map.get(prefix)

            return sens_percent, effect
        except Exception:
            return 50, None


    async def async_turn_on(self, **kwargs):
        """Turn on the light with optional parameters."""
        # Determine new values from kwargs
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._brightness)
        rgb_color = kwargs.get(ATTR_RGB_COLOR, self._attr_rgb_color)
        requested_rgb_change = ATTR_RGB_COLOR in kwargs
        requested_brightness_change = ATTR_BRIGHTNESS in kwargs
        b1_static_color_request = (
            self.is_b1_model
            and requested_rgb_change
            and self._is_b1_white_like(rgb_color)
        )
        b1_rgb_brightness_request = (
            self.is_b1_model
            and requested_brightness_change
            and not requested_rgb_change
            and self._mode == 1
            and not self._is_b1_white_like(self._attr_rgb_color)
        )
        requested_effect = kwargs.get(ATTR_EFFECT)
        effect = requested_effect if requested_effect is not None else self._effect
        send_effect = self.EFFECT_SOLID if effect == self.EFFECT_NONE else effect
        if send_effect in self.SPECIAL_EFFECTS:
            send_effect = self.EFFECT_SOLID
        
        # Update state optimistically
        self._is_on = True
        self._brightness = brightness
        if self.is_b1_model and (b1_rgb_brightness_request or (requested_rgb_change and not b1_static_color_request)):
            self._mode = 1
        elif self.is_b1_model and effect == self.EFFECT_NONE:
            self._mode = self._get_b1_static_payload()["d2"]
        else:
            self._mode = 2
        
        # When color changes on the main light, set all segments to the same color
        if requested_rgb_change:
            self._attr_rgb_color = rgb_color
            if not self.is_b1_model:
                # set all segment colors to the main color
                self._segment_colors = [tuple(int(c) for c in rgb_color)] * 25
        
        if ATTR_EFFECT in kwargs:
            self._effect = effect
        elif self._effect in self.SPECIAL_EFFECTS:
            self._effect = self.EFFECT_NONE
        
        # Send command based on effect
        if b1_static_color_request:
            self._effect = self.EFFECT_NONE
            await self._send_effect_command()
        elif self.is_b1_model and (requested_rgb_change or b1_rgb_brightness_request):
            await self._send_b1_rgb_command(self._attr_rgb_color)
        elif send_effect in self.SPECIAL_EFFECTS:
            # special effects use d2=3 (d60)
            await self._send_special_effect_command(send_effect)
        else:
            # regular effects use d2=2 (d50)
            await self._send_effect_command()

        
        # update HA states: main + segments
        self.async_write_ha_state()
        # update segment entities attached to this device if present
        try:
            segments = self.hass.data[DOMAIN][self._entry_id].get('segments', {}).get(self._did, [])
            for seg in segments:
                try:
                    seg.async_write_ha_state()
                except Exception:
                    pass
        except Exception:
            pass


    def _hex_to_speed(self, hex_str):
        """Convert 4-digit hex speed value to percentage (0-100)"""
        code = hex_str.upper()
        if code == "1000":
            return 0.0
        raw = int(code[1:], 16)
        percent = np.exp((raw - 597.75) / -117.41) - 1
        return float(max(0, min(100, percent)))

    def _speed_to_hex(self, speed):
        """Convert percentage (0-100) to 4-digit hex speed value"""
        if speed <= 0:
            return "1000"
        raw = int(round(-117.41 * np.log(speed + 1) + 597.75))
        return f"0{raw:03X}"
    
    def _generate_d50_string(self):
        """
        Generate d50 string following the grouped-color format:
        N01:P1000{num_groups}{colors}{F21000}{num_groups}{lengths}U3V3{effect};
        - colors: each 6 hex (RGB)
        - lengths: each 4 hex representing number of segments in that group (hex)
        """
        # compress contiguous segment colors into groups
        groups = []
        for col in self._segment_colors:
            if not groups:
                groups.append([col, 1])
            else:
                if col == groups[-1][0]:
                    groups[-1][1] += 1
                else:
                    groups.append([col, 1])

        # normalize/truncate/extend to ensure 25 segments
        total = sum(g[1] for g in groups)
        if total != 25:
            if total < 25:
                groups[-1][1] += (25 - total)
            else:
                while sum(g[1] for g in groups) > 25:
                    excess = sum(g[1] for g in groups) - 25
                    if groups[-1][1] > excess:
                        groups[-1][1] -= excess
                    else:
                        groups.pop()

        num_groups = len(groups)

        # build colors string (each color 6 hex)
        colors_parts = []
        for (r, g, b), cnt in groups:
            colors_parts.append(f"{int(r):02X}{int(g):02X}{int(b):02X}")
        colors_str = ''.join(colors_parts)

        # build lengths string (each as 4-hex uppercase)
        lengths_parts = []
        for (_, count) in groups:
            lengths_parts.append(f"{count:04X}")
        lengths_str = ''.join(lengths_parts)

        # build effect tail (reuse your existing logic)
        effect = ""
        if self._effect in (self.EFFECT_NONE, self.EFFECT_SOLID):  # Solid effect
            effect = "000640000E1"
        elif self._effect == self.EFFECT_BREATH:  # Breath effect
            effect = "000640000E4" + self._speed_to_hex(self._speed) + "0000" + self._speed_to_hex(self._speed) + "1664"
        elif self._effect == self.EFFECT_GRADIENT:  # Gradient
            effect = "100640000E3" + self._speed_to_hex(self._speed) + "C2O6" + self._speed_to_hex(self._speed)
        elif self._effect == self.EFFECT_CLOCKWISE:  # Clockwise
            effect = "00164" + self._speed_to_hex(self._speed) + "E1"
        elif self._effect == self.EFFECT_COUNTERCLOCKWISE:  # Counterclockwise
            effect = "00264" + self._speed_to_hex(self._speed) + "E1"
        elif self._effect == self.EFFECT_CIRCULAR:  # Circular
            effect = "100640000E1" + "C2O6" + self._speed_to_hex(self._speed)

        # Compose the grouped d50
        d50 = f"N01:P1000{num_groups}{colors_str}F21000{num_groups}{lengths_str}U3V3{effect};"
        return d50


    def _parse_d50(self, d50_str):
        """Parse grouped d50 string for effect and segment colours and primary color"""
        try:
            # Reset to defaults
            self._effect = self.EFFECT_NONE
            self._speed = 50

            # Find P1000 block and F21000 marker
            p_idx = d50_str.find('P1000')
            if p_idx == -1:
                # fallback: try single-color old format
                match = re.search(r'P10001([0-9A-F]{6})', d50_str)
                if match:
                    hex_color = match.group(1)
                    r = int(hex_color[0:2], 16)
                    g = int(hex_color[2:4], 16)
                    b = int(hex_color[4:6], 16)
                    self._segment_colors = [(r, g, b)] * 25
                    self._attr_rgb_color = (r, g, b)
                # continue to parse effect below
            else:
                f_idx = d50_str.find('F21000', p_idx)
                if f_idx == -1:
                    raise ValueError("Missing F21000 after P1000 block")

                # block contains ascii num_groups + colors_hex
                block = d50_str[p_idx + len('P1000') : f_idx]

                # we must determine how many ASCII digits form num_groups (k)
                num_groups = None
                k_used = None
                for k in range(1, 4):  # try 1..3 digits for safety
                    if len(block) >= k and block[:k].isdigit():
                        candidate = int(block[:k])
                        if len(block) - k == 6 * candidate:
                            num_groups = candidate
                            k_used = k
                            break

                if num_groups is None:
                    raise ValueError("Could not deduce num_groups / colors length from d50")

                colors_hex = block[k_used:]
                colors = [colors_hex[i*6:(i+1)*6] for i in range(num_groups)]
                colors_rgb = [(int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)) for c in colors]

                # read second num_groups with the same digit-length k_used
                num_groups2_str = d50_str[f_idx + 6 : f_idx + 6 + k_used]
                num_groups2 = int(num_groups2_str) if num_groups2_str.isdigit() else None

                # read lengths: next num_groups * 4 hex chars
                lengths_hex = d50_str[f_idx + 6 + k_used : f_idx + 6 + k_used + num_groups * 4]
                if len(lengths_hex) < num_groups * 4:
                    raise ValueError("Not enough length hex data in d50")

                lengths = [int(lengths_hex[i*4:(i+1)*4], 16) for i in range(num_groups)]

                # expand to 25 segments
                segs = []
                for (col, cnt) in zip(colors_rgb, lengths):
                    segs.extend([col] * cnt)

                # normalize to 25 segments
                if len(segs) < 25:
                    if segs:
                        segs.extend([segs[-1]] * (25 - len(segs)))
                    else:
                        segs = [(255,255,255)] * 25
                elif len(segs) > 25:
                    segs = segs[:25]

                self._segment_colors = segs
                self._attr_rgb_color = self._segment_colors[0]

            # Parse effect and speed as before
            if "000640000E1" in d50_str:
                self._effect = self.EFFECT_NONE
            elif breath_match := re.search(r'000640000E4([0-9A-F]{4})0000[0-9A-F]{4}1664', d50_str):
                self._effect = self.EFFECT_BREATH
                speed_hex = breath_match.group(1)
                self._speed = self._hex_to_speed(speed_hex)
            elif gradient_match := re.search(r'100640000E3([0-9A-F]{4})C2O6[0-9A-F]{4}', d50_str):
                self._effect = self.EFFECT_GRADIENT
                speed_hex = gradient_match.group(1)
                self._speed = self._hex_to_speed(speed_hex)
            elif clockwise_match := re.search(r'00164([0-9A-F]{4})E1', d50_str):
                self._effect = self.EFFECT_CLOCKWISE
                speed_hex = clockwise_match.group(1)
                self._speed = self._hex_to_speed(speed_hex)
            elif counter_match := re.search(r'00264([0-9A-F]{4})E1', d50_str):
                self._effect = self.EFFECT_COUNTERCLOCKWISE
                speed_hex = counter_match.group(1)
                self._speed = self._hex_to_speed(speed_hex)
            elif circular_match := re.search(r'100640000E1C2O6([0-9A-F]{4})', d50_str):
                self._effect = self.EFFECT_CIRCULAR
                speed_hex = circular_match.group(1)
                self._speed = self._hex_to_speed(speed_hex)

            # Update segment entities if present
            try:
                segments = self.hass.data[DOMAIN][self._entry_id].get('segments', {}).get(self._did, [])
                for seg in segments:
                    try:
                        seg.async_write_ha_state()
                    except Exception:
                        pass
            except Exception:
                pass

        except Exception as e:
            _LOGGER.error("Error parsing d50: %s", e)
            # fallback
            self._segment_colors = [(255, 255, 255)] * 25
            self._attr_rgb_color = (255, 255, 255)
            self._speed = 50


    @property
    def is_on(self):
        return self._is_on

    @property
    def brightness(self):
        return self._brightness

    @property
    def effect(self):
        return self._effect

    async def _send_special_effect_command(self, effect):
        """Send command for special (d2=3) effects using d60.
           Uses self._sensitivity as 0..100 percent and encodes to 0x00..0x63.
        """
        try:
            prefix = self.SPECIAL_EFFECT_TO_D60_PREFIX.get(effect)
            if not prefix:
                _LOGGER.error("Unknown special effect: %s", effect)
                return

            # Read sensitivity as percent 0..100
            sens_percent = int(round(getattr(self, "_sensitivity", 50)))
            # clamp percent
            sens_percent = max(0, min(100, sens_percent))

            # Encode percent (0..100) -> hex_val (0..0x63)
            MAX_HEX = 0x63
            hex_val = int(round(sens_percent * MAX_HEX / 100))
            hex_val = max(0, min(MAX_HEX, hex_val))

            sens_hex = f"{hex_val:02X}"  # two hex digits
            d60_value = f"{prefix}{sens_hex}0000"

            payload = {
                "d1": 1,
                "d2": 3,
                "d60": d60_value,
                "d52": self._map_ha_brightness(self._brightness)
            }
            await self._send_mqtt_command(payload)
        except Exception as e:
            _LOGGER.error("Failed to send special effect command %s: %s", effect, e)

    async def _ensure_solid_mode(self):
        """Normalize the device back to static light mode."""
        if self._normalizing_effect or not self._is_on:
            return

        self._normalizing_effect = True
        try:
            self._effect = self.EFFECT_NONE
            self._mode = self._get_b1_static_payload(self._brightness)["d2"] if self.is_b1_model else 2
            await self._send_effect_command()
        except Exception as e:
            _LOGGER.error("Failed to normalize %s to solid mode: %s", self.name, e)
        finally:
            self._normalizing_effect = False

    async def _send_b1_rgb_command(self, rgb_color):
        """Send a B1 RGB payload using the same fields the official app writes."""
        payload = self._build_b1_rgb_payload(rgb_color, self._brightness)
        _LOGGER.info("B1 rgb-mode payload for %s (%s): %s", self.name, self._did, payload)
        await self._send_mqtt_command(payload)


    async def _send_effect_command(self):
        """Send command for effect modes"""
        if self._should_skip_d50_for_static_mode():
            payload = self._get_b1_static_payload(self._brightness)
            _LOGGER.info(
                "B1 static-mode payload for %s (%s): %s",
                self.name,
                self._did,
                payload,
            )
        else:
            payload = {
                "d1": 1,
                "d52": self._map_ha_brightness(self._brightness)
            }
            payload["d2"] = 2
            payload["d50"] = self._generate_d50_string()
        await self._send_mqtt_command(payload)

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        payload = {"d1": 0}
        await self._send_mqtt_command(payload)
        self._is_on = False
        self.async_write_ha_state()

    async def _send_mqtt_command(self, payload: dict):
        """Send command via MQTT"""
        topic = f"le/{self._did}/prp/set"
        full_payload = {
            "id": random.randint(0, 1000000000),
            "t": int(time.time()),
            "d": payload
        }
        try:
            await self._mqtt_client.publish(topic, json.dumps(full_payload))
            _LOGGER.debug("Sent MQTT command: %s - %s", topic, full_payload)
            if self.is_b1_model:
                _LOGGER.info(
                    "B1 command for %s (%s): %s",
                    self.name,
                    self._did,
                    full_payload,
                )
        except Exception as e:
            _LOGGER.error("Failed to send MQTT command: %s", e)
            
    async def async_added_to_hass(self):
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        # Request initial state
        await self._request_state_update()

    async def _request_state_update(self):
        """Request current state from device."""
        topic = f"le/{self._did}/prp/get"
        # Request both switch state and brightness
        payload = json.dumps({"d": ["d1", "d2", "d3", "d4", "d5", "d30", "d50", "d52", "d60", "online"]})
        try:
            await self._mqtt_client.publish(topic, payload)
            _LOGGER.debug("Requested state update for %s", self.name)
            if self.is_b1_model:
                _LOGGER.info("B1 state request for %s (%s): %s", self.name, self._did, payload)
        except Exception as e:
            _LOGGER.error("Failed to request state update: %s", e)


class LeproSegmentLight(LightEntity):
    """Represents a single segment of a segmented Lepro LED."""
    def __init__(self, parent: LeproLedLight, index: int):
        self._parent = parent
        self._index = index
        self._attr_has_entity_name = True
        self._attr_translation_key = "segment"
        self._attr_unique_id = f"{parent._did}_segment_{str(index+1).rjust(2, '0')}"
        self._attr_device_info = parent._attr_device_info
        self._attr_color_mode = parent._attr_color_mode
        self._attr_supported_color_modes = parent._attr_supported_color_modes
        self._attr_supported_features = LightEntityFeature.EFFECT

    # placeholder usato nei JSON: {index}
    @property
    def translation_placeholders(self) -> dict:
        return {"index": str(self._index + 1).rjust(2, "0")}
        
    @property
    def is_on(self):
        return self._parent._is_on

    @property
    def brightness(self):
        return self._parent._brightness

    @property
    def effect(self):
        return self._parent._effect

    @property
    def rgb_color(self):
        segs = getattr(self._parent, "_segment_colors", [(255,255,255)]*25)
        if len(segs) > self._index:
            return segs[self._index]
        return (255,255,255)

    # HA property used by UI (preferred attribute already in parent):
    @property
    def available(self):
        return True

    async def async_turn_on(self, **kwargs):
        """Turn on (or change color) for this segment. Updating the parent triggers d50 send."""
        # Update brightness if provided
        if ATTR_BRIGHTNESS in kwargs:
            new_brightness = kwargs[ATTR_BRIGHTNESS]
            self._parent._brightness = new_brightness

        # Update color if provided
        if ATTR_RGB_COLOR in kwargs:            
            # read new color, default to current segment color
            new_color = kwargs.get(ATTR_RGB_COLOR, self.rgb_color)
            # update parent's segment color
            seg_colors = list(self._parent._segment_colors)
            seg_colors[self._index] = tuple(int(c) for c in new_color)
            self._parent._segment_colors = seg_colors
            # If this is segment 0 (first) update parent's primary color too
            if self._index == 0:
                self._parent._attr_rgb_color = tuple(int(c) for c in new_color)
            # send updated d50 via parent

        try:
            if self._parent._effect in self._parent.SPECIAL_EFFECTS:
                # Restore old behavior: do not force-exit special effects on segment changes
                await self._parent._send_special_effect_command(self._parent._effect)
            else:
                self._parent._mode = 2
                await self._parent._send_effect_command()
        except Exception as e:
            _LOGGER.error("Error sending d50 after segment change: %s", e)

        # update states: parent + all segments
        try:
            self._parent.async_write_ha_state()
            segments = self.hass.data[DOMAIN][self._parent._entry_id].get('segments', {}).get(self._parent._did, [])
            for seg in segments:
                try:
                    seg.async_write_ha_state()
                except Exception:
                    pass
        except Exception:
            pass

    async def async_turn_off(self, **kwargs):
        """Turning off a single segment maps to turning off the parent device."""
        await self._parent.async_turn_off(**kwargs)


async def download_cert_file(session, url, path, headers):
    """Download a certificate file asynchronously."""
    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            raise Exception(f"Failed to download {url}: {resp.status}")
        data = await resp.read()
        async with aiofiles.open(path, 'wb') as f:
            await f.write(data)

def create_ssl_context(root_ca_path, client_cert_path, keyfile_path):
    """Create SSL context in a thread-safe manner."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=root_ca_path)
    context.load_cert_chain(certfile=client_cert_path, keyfile=keyfile_path)
    return context

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up Lepro LED lights from config entry."""
    config = hass.data["lepro_led"][entry.entry_id]
    account = config["account"]
    password = config["password"]
    
    # Create a mutable copy of the config
    config_data = dict(config)
    
    # Generate persistent MAC if not exists
    if "persistent_mac" not in config_data:
        # Create a hash of the account to generate a persistent MAC
        mac_hash = hashlib.md5(config_data["account"].encode()).hexdigest()
        persistent_mac = f"02:{mac_hash[0:2]}:{mac_hash[2:4]}:{mac_hash[4:6]}:{mac_hash[6:8]}:{mac_hash[8:10]}"
        config_data["persistent_mac"] = persistent_mac
        
        # Save updated config to the entry
        hass.config_entries.async_update_entry(entry, data=config_data)
        _LOGGER.info("Generated persistent MAC: %s", persistent_mac)
    
    # Use the persistent MAC from config_data
    mac = config_data["persistent_mac"]
    language = config_data.get("language", "en")
    fcm_token = config_data.get("fcm_token", "dfi8s76mRTCxRxm3UtNp2z:APA91bHWMEWKT9CgNfGJ961jot2qgfYdWePbO5sQLovSFDI7U_H-ulJiqIAB2dpZUUrhzUNWR3OE_eM83i9IDLk1a5ZRwHDxMA_TnGqdpE8H-0_JML8pBFA")

    region = config_data.get("region", "eu")
    api_host = REGIONS.get(region, REGIONS["eu"])

    login_url = f"https://{api_host}{LOGIN_PATH}"
    family_list_url = f"https://{api_host}{FAMILY_LIST_PATH}"
    user_profile_url = f"https://{api_host}{USER_PROFILE_PATH}"
    device_list_url = f"https://{api_host}{DEVICE_LIST_PATH}"

    _LOGGER.info("Using Lepro API region: %s (%s)", region, api_host)
    
    # Update hass.data with the new config
    hass.data["lepro_led"][entry.entry_id] = config_data
    
    # ... rest of the setup code ...
    # 1) Create certificate directory
    cert_dir = os.path.join(hass.config.config_dir, ".lepro_led")
    if not os.path.exists(cert_dir):
        await hass.async_add_executor_job(os.makedirs, cert_dir)

    root_ca_path = os.path.join(cert_dir, f"{entry.entry_id}_root_ca.pem")
    client_cert_path = os.path.join(cert_dir, f"{entry.entry_id}_client_cert.pem")
    keyfile_path = os.path.join(os.path.dirname(__file__), "client_key.pem")

    async with aiohttp.ClientSession() as session:
        # 1) Login and get bearer token
        bearer_token = await async_login(session, account, password, mac, login_url, api_host, language, fcm_token)
        if bearer_token is None:
            _LOGGER.error("Failed to login to Lepro API")
            return

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Accept-Encoding": "gzip",
            "App-Version": "1.0.9.202",
            "Device-Model": "custom_integration",
            "Device-System": "custom",
            "GMT": "+0",
            "Host": api_host,
            "Language": language,
            "Platform": "2",
            "Screen-Size": "1536*2048",
            "Slanguage": language,
            "User-Agent": "LE/1.0.9.202 (Custom Integration)",
        }

        # 2) Get user profile to find uid and MQTT info
        user_url = user_profile_url
        timestamp = str(int(time.time()))
        headers["Timestamp"] = timestamp
        
        async with session.get(user_url, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.error("Failed to get user profile from Lepro API")
                return
            user_data = await resp.json()

        try:
            uid = user_data["data"]["uid"]
            mqtt_info = user_data["data"]["mqtt"]
        except KeyError as e:
            _LOGGER.error("Failed to parse user profile response: %s", e)
            return

        # 3) Download certificates within the same session
        try:
            await download_cert_file(session, mqtt_info["root"], root_ca_path, headers)
            await download_cert_file(session, mqtt_info["cert"], client_cert_path, headers)
        except Exception as e:
            _LOGGER.error("Certificate download failed: %s", e)
            return

        # 4) Get family list to find fid
        family_url = family_list_url.format(timestamp=timestamp)
        async with session.get(family_url, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.error("Failed to get family list from Lepro API")
                return
            family_data = await resp.json()

        try:
            fid = family_data["data"]["list"][0]["fid"]
        except (KeyError, IndexError) as e:
            _LOGGER.error("Failed to parse fid from family list response: %s", e)
            return

        # 5) Get device list by fid
        timestamp = str(int(time.time()))
        device_url = device_list_url.format(fid=fid, timestamp=timestamp)
        headers["Timestamp"] = timestamp

        async with session.get(device_url, headers=headers) as resp:
            if resp.status != 200:
                _LOGGER.error("Failed to get device list from Lepro API")
                return
            device_data = await resp.json()

        devices = device_data.get("data", {}).get("list", [])
        if not devices:
            _LOGGER.warning("No devices found in Lepro account")
            return

    # 6) Create SSL context in executor thread
    try:
        ssl_context = await hass.async_add_executor_job(
            create_ssl_context, 
            root_ca_path, 
            client_cert_path, 
            keyfile_path
        )
    except Exception as e:
        _LOGGER.error("Failed to create SSL context: %s", e)
        return

    # 7) Create MQTT client
    client_id_suffix = hashlib.sha256(entry.entry_id.encode()).hexdigest()[:32]
    client_id = f"lepro-app-{client_id_suffix}"
    
    mqtt_client = MQTTClientWrapper(
        hass,
        host=mqtt_info["host"],
        port=int(mqtt_info["port"]),
        ssl_context=ssl_context,
        client_id=client_id
    )
    
    try:
        await mqtt_client.connect()
    except Exception as e:
        _LOGGER.error("MQTT connection failed: %s", e)
        return
    
    # 8) Create entities
    entities = []
    device_entity_map = {}
    segments_map = {}
    for device in devices:
        entity = LeproLedLight(device, mqtt_client, entry.entry_id)
        entities.append(entity)
        device_entity_map[str(device['did'])] = entity

        # If this is a segmented series (S1-5) create 25 segment lights
        series = device.get("series", "") or ""
        if "S1-5" in series:
            segs = []
            for idx in range(25):
                seg_entity = LeproSegmentLight(entity, idx)
                entities.append(seg_entity)
                segs.append(seg_entity)
            segments_map[str(device['did'])] = segs
    
    # 9) Message handler
    # Update the message handler to process all relevant fields
    async def handle_mqtt_message(message):
        try:
            topic = message.topic.value
            payload = json.loads(message.payload.decode())
            _LOGGER.debug("Received MQTT message: %s - %s", topic, payload)
            
            parts = topic.split('/')
            if len(parts) < 4 or parts[0] != "le":
                return
                
            did = parts[1]
            message_type = parts[3]
            entity = device_entity_map.get(did)
            
            if not entity:
                return

            if entity.is_b1_model:
                _LOGGER.info(
                    "B1 raw MQTT for %s (%s) topic=%s type=%s payload=%s",
                    entity.name,
                    did,
                    topic,
                    message_type,
                    payload,
                )
                
            # Handle different message types
            if message_type in ["rpt", "set", "getr"]:
                data = payload.get('d', {})
                if entity.is_b1_model:
                    b1_fields = {
                        key: data.get(key)
                        for key in ["d1", "d2", "d3", "d4", "d5", "d30", "d50", "d52", "d60", "online"]
                        if key in data
                    }
                    _LOGGER.info(
                        "B1 state update for %s (%s) topic=%s: %s",
                        entity.name,
                        did,
                        topic,
                        b1_fields,
                    )
                    if any(key in data for key in ["d2", "d3", "d4", "d5"]):
                        _LOGGER.info(
                            "B1 RGB sample for %s (%s): d2=%s d3=%s d4=%s d5=%s",
                            entity.name,
                            did,
                            data.get("d2"),
                            data.get("d3"),
                            data.get("d4"),
                            data.get("d5"),
                        )
                
                # Update basic state
                if 'd1' in data:
                    entity._is_on = bool(data['d1'])
                
                # Update mode
                if 'd2' in data:
                    entity._mode = data['d2']
                
                # Update brightness
                if entity.is_b1_model and entity._mode == 0 and 'd3' in data:
                    entity._brightness = entity._map_device_brightness(data['d3'])
                    entity._attr_brightness = entity._brightness
                elif 'd52' in data:
                    entity._brightness = entity._map_device_brightness(data['d52'])
                    entity._attr_brightness = entity._brightness

                entity._update_b1_static_state(data)
                entity._update_b1_rgb_state(data)
                
                # Update effect and colors
                if 'd50' in data:
                    entity._parse_d50(data['d50'])
                
                # Update d60: special effects and sensitivity
                if 'd60' in data:
                    sens, parsed_effect = entity._parse_d60(data['d60'])
                    # update sensitivity
                    entity._sensitivity = sens
                    # If parser recognizes a special effect code, set it
                    if parsed_effect:
                        entity._effect = parsed_effect

                # Normalize devices that report a special-effect mode back to solid light mode.
                if entity.is_b1_model and entity._mode == 3:
                    entity._effect = entity.EFFECT_NONE
                    entity._mode = 2
                    if entity._is_on and not entity._normalizing_effect:
                        entity.hass.async_create_task(entity._ensure_solid_mode())

                # update main + segments states
                entity.async_write_ha_state()

                # NEW: also refresh any number entities (speed / sensitivity) linked to this device
                try:
                    numbers_map = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("numbers", {})
                    for num in numbers_map.get(did, []):
                        try:
                            num.async_write_ha_state()
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    for seg in segments_map.get(did, []):
                        seg.async_write_ha_state()
                except Exception:
                    pass

                _LOGGER.debug("Updated state for %s: on=%s, mode=%s, effect=%s, brightness=%s, speed=%s, rgb=%s, sensitivity=%s", 
                             entity.name, entity._is_on, entity._mode, entity._effect, entity._brightness, entity._speed, entity._segment_colors[0], entity._sensitivity)
                if entity.is_b1_model:
                    _LOGGER.info(
                        "B1 normalized state for %s (%s): on=%s mode=%s effect=%s brightness=%s rgb=%s",
                        entity.name,
                        did,
                        entity._is_on,
                        entity._mode,
                        entity._effect,
                        entity._brightness,
                        entity._segment_colors[0],
                    )
                    
        except Exception as e:
            _LOGGER.error("Error processing MQTT message: %s", e)
   
    mqtt_client.set_message_callback(handle_mqtt_message)
    
    # 10) Subscribe and start
    await mqtt_client.subscribe(f"le/{client_id_suffix}/act/app/exe")
    for did in device_entity_map.keys():
        await mqtt_client.subscribe(f"le/{did}/prp/#")
    
    # Store for cleanup
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][entry.entry_id] = {
        'mqtt_client': mqtt_client,
        'entities': entities,
        'segments': segments_map
    }
    
    async_add_entities(entities)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload MQTT client and entities."""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data:
        return True
        
    # Disconnect MQTT client
    await data['mqtt_client'].disconnect()
    
    # Remove entities
    for entity in data['entities']:
        await entity.async_remove()
        
    hass.data[DOMAIN].pop(entry.entry_id)
    return True
    
