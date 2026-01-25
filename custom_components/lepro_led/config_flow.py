from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN, API_REGIONS

DATA_SCHEMA = vol.Schema({
    vol.Required("account"): str,
    vol.Required("password"): str,
    vol.Optional("region", default="fe"): vol.In({
        "fe": "Far East / Asia Pacific",
        "eu": "Europe",
        "us": "United States",
    }),
    vol.Optional("language", default="en"): str,
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
