from homeassistant import config_entries
from homeassistant.helpers import selector
import voluptuous as vol
from .const import DOMAIN, REGIONS

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
            return self.async_create_entry(title="Lepro LED", data=data)

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
