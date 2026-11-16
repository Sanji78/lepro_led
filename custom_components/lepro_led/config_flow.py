from homeassistant import config_entries
from homeassistant.helpers import selector
import voluptuous as vol
import asyncio
import time
import hashlib
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, REGIONS, LOGIN_PATH
import logging

_LOGGER = logging.getLogger(__name__)

REGION_OPTIONS = [
    selector.SelectOptionDict(value="eu", label="Europe"),
    selector.SelectOptionDict(value="us", label="United States"),
    selector.SelectOptionDict(value="na", label="North America"),
    selector.SelectOptionDict(value="fe", label="Far East"),
]
LANGUAGE_OPTIONS = [
    selector.SelectOptionDict(value="en", label="English"),
    selector.SelectOptionDict(value="it", label="Italiano"),
    selector.SelectOptionDict(value="ja", label="Japanese"),
]

DATA_SCHEMA = vol.Schema({
    vol.Required("account"): str,
    vol.Required("password"): str,
    vol.Optional("region", default="eu"): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=REGION_OPTIONS,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    ),
    vol.Optional("language", default="en"): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=LANGUAGE_OPTIONS,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    ),
})

class LeproLedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # Create a new mutable dictionary for the user input
            data = dict(user_input)
            
            # Don't include persistent_mac here - it will be generated later
            session = async_get_clientsession(self.hass)
            account = data["account"]
            language = data.get("language", "en")
            mac_hash = hashlib.md5(account.encode()).hexdigest()
            mac = f"02:{mac_hash[0:2]}:{mac_hash[2:4]}:{mac_hash[4:6]}:{mac_hash[6:8]}:{mac_hash[8:10]}"
            fcm_token = "dfi8s76mRTCxRxm3UtNp2z:APA91bHWMEWKT9CgNfGJ961jot2qgfYdWePbO5sQLovSFDI7U_H-ulJiqIAB2dpZUUrhzUNWR3OE_eM83i9IDLk1a5ZRwHDxMA_TnGqdpE8H-0_JML8pBFA"

            from .light import async_login

            # Candidate regions: the one picked in the form first, then the others
            candidates = [data.get("region", "eu")]
            candidates += [r for r in REGIONS if r not in candidates]

            # Ask Lepro which region the account belongs to (same bootstrap call used by the official app)
            try:
                ts = str(int(time.time()))
                async with session.post(
                    "https://api-iot.lepro.com/user/region",
                    data={"account": account, "timestamp": ts},
                    headers={"Platform": "2", "Timestamp": ts, "Language": language, "Slanguage": language, "User-Agent": "LE/1.0.9.202 (Custom Integration)"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    region_json = await resp.json(content_type=None)
                api_host_found = (region_json.get("data") or {}).get("apiHost")
                for r, h in REGIONS.items():
                    if h == api_host_found:
                        candidates = [r] + [c for c in candidates if c != r]
                        break
            except Exception:
                pass

            token = None
            found_region = None
            had_response = False
            _LOGGER.debug("Lepro config flow: region candidates order: %s", candidates)
            for region in candidates:
                api_host = REGIONS[region]
                try:
                    token = await async_login(
                        session, account, data["password"], mac,
                        f"https://{api_host}{LOGIN_PATH}", api_host, language, fcm_token,
                    )
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    _LOGGER.warning("Lepro config flow: region %s (%s) not reachable: %s", region, api_host, err)
                    continue
                except Exception:
                    _LOGGER.exception("Lepro config flow: unexpected error on region %s", region)
                    errors["base"] = "unknown"
                    break
                had_response = True
                _LOGGER.debug("Lepro config flow: region %s login ok=%s", region, bool(token))
                if token:
                    found_region = region
                    break

            if found_region:
                data["region"] = found_region
                # Lepro rejects a second identical login in the same second: wait before entry setup logs in again
                await asyncio.sleep(3)
                return self.async_create_entry(title="Lepro LED", data=data)
            if "base" not in errors:
                errors["base"] = "invalid_auth" if had_response else "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
