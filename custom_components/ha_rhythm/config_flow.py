"""Config flow for HA Rhythm."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    PROVIDERS,
    DEFAULT_PROVIDER,
    DEFAULT_SCAN_DAYS,
    CONF_AI_PROVIDER,
    CONF_AI_KEY,
    CONF_AI_BASE_URL,
    CONF_AI_MODEL,
    CONF_SCAN_DAYS,
)

STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_AI_PROVIDER, default=DEFAULT_PROVIDER): vol.In(PROVIDERS),
    vol.Optional(CONF_AI_KEY, default=""): str,
    vol.Optional(CONF_AI_BASE_URL, default=""): str,
    vol.Optional(CONF_AI_MODEL, default=""): str,
    vol.Optional(CONF_SCAN_DAYS, default=DEFAULT_SCAN_DAYS): vol.All(int, vol.Range(min=7, max=90)),
})


class HaRhythmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for HA Rhythm."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="HA Rhythm", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HaRhythmOptionsFlow(config_entry)


class HaRhythmOptionsFlow(config_entries.OptionsFlow):
    """Options flow for HA Rhythm."""

    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        data = self._entry.data
        opts = self._entry.options

        def _get(key, default):
            return opts.get(key, data.get(key, default))

        schema = vol.Schema({
            vol.Required(CONF_AI_PROVIDER, default=_get(CONF_AI_PROVIDER, DEFAULT_PROVIDER)): vol.In(PROVIDERS),
            vol.Optional(CONF_AI_KEY, default=_get(CONF_AI_KEY, "")): str,
            vol.Optional(CONF_AI_BASE_URL, default=_get(CONF_AI_BASE_URL, "")): str,
            vol.Optional(CONF_AI_MODEL, default=_get(CONF_AI_MODEL, "")): str,
            vol.Optional(CONF_SCAN_DAYS, default=_get(CONF_SCAN_DAYS, DEFAULT_SCAN_DAYS)): vol.All(int, vol.Range(min=7, max=90)),
        })

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="init", data_schema=schema)
