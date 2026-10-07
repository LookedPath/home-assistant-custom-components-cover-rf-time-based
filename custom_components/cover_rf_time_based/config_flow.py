"""UI setup and calibration options for time-based covers."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.const import CONF_DEVICE_CLASS, CONF_NAME
from homeassistant.core import callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_ALWAYS_CONFIDENT,
    CONF_AVAILABILITY_TPL,
    CONF_CLOSE_SCRIPT_ENTITY_ID,
    CONF_CONTROL_TYPE,
    CONF_COVER_ENTITY_ID,
    CONF_OPEN_SCRIPT_ENTITY_ID,
    CONF_SEND_STOP_AT_ENDS,
    CONF_STOP_SCRIPT_ENTITY_ID,
    CONF_TRAVELLING_TIME_DOWN,
    CONF_TRAVELLING_TIME_UP,
    DEFAULT_ALWAYS_CONFIDENT,
    DEFAULT_DEVICE_CLASS,
    DEFAULT_SEND_STOP_AT_ENDS,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
)
from .schema import BASE_DEVICE_SCHEMA, COVER_DEVICE_SCHEMA, SCRIPT_DEVICE_SCHEMA

SETTINGS = (
    CONF_TRAVELLING_TIME_UP,
    CONF_TRAVELLING_TIME_DOWN,
    CONF_SEND_STOP_AT_ENDS,
    CONF_ALWAYS_CONFIDENT,
    CONF_DEVICE_CLASS,
    CONF_AVAILABILITY_TPL,
)
SCRIPT_FIELDS = (
    CONF_OPEN_SCRIPT_ENTITY_ID,
    CONF_CLOSE_SCRIPT_ENTITY_ID,
    CONF_STOP_SCRIPT_ENTITY_ID,
)


def settings_schema(defaults):
    """Use selectors for the calibration and display settings."""
    fields = {
        vol.Required(key, default=defaults.get(key, DEFAULT_TRAVEL_TIME)): selector.NumberSelector(
            {"min": 1, "step": 1, "mode": "box"}
        )
        for key in (CONF_TRAVELLING_TIME_UP, CONF_TRAVELLING_TIME_DOWN)
    }
    fields.update(
        {
            vol.Required(
                CONF_SEND_STOP_AT_ENDS,
                default=defaults.get(CONF_SEND_STOP_AT_ENDS, DEFAULT_SEND_STOP_AT_ENDS),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_ALWAYS_CONFIDENT,
                default=defaults.get(CONF_ALWAYS_CONFIDENT, DEFAULT_ALWAYS_CONFIDENT),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DEVICE_CLASS, default=defaults.get(CONF_DEVICE_CLASS, DEFAULT_DEVICE_CLASS)
            ): selector.SelectSelector({"options": [value.value for value in CoverDeviceClass]}),
            vol.Optional(
                CONF_AVAILABILITY_TPL,
                description={"suggested_value": defaults.get(CONF_AVAILABILITY_TPL, "")},
            ): selector.TemplateSelector(),
        }
    )
    return fields


def validate_settings(user_input):
    """Validate settings, returning serializable values for config entries."""
    config = dict(user_input)
    if not config.get(CONF_AVAILABILITY_TPL, "").strip():
        config.pop(CONF_AVAILABILITY_TPL, None)
    validated = BASE_DEVICE_SCHEMA({CONF_NAME: "Cover", **config})
    template = validated.get(CONF_AVAILABILITY_TPL)
    if template is not None:
        template.ensure_valid()
        validated[CONF_AVAILABILITY_TPL] = template.template
    return {key: validated[key] for key in SETTINGS if key in validated}


class CoverTimeBasedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Choose the command source and configure a cover."""

    VERSION = 1

    def __init__(self):
        self._name = None
        self._control_type = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CoverTimeBasedOptionsFlow()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._name = user_input[CONF_NAME]
            self._control_type = user_input[CONF_CONTROL_TYPE]
            return await self._async_source_step(self._control_type)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_CONTROL_TYPE, default="cover"): selector.SelectSelector(
                        {
                            "options": ["cover", "scripts"],
                            "translation_key": "control_type",
                        }
                    ),
                }
            ),
        )

    async def async_step_cover(self, user_input=None):
        return await self._async_source_step("cover", user_input)

    async def async_step_scripts(self, user_input=None):
        return await self._async_source_step("scripts", user_input)

    async def _async_source_step(self, control_type, user_input=None):
        errors = {}
        registry = er.async_get(self.hass)
        source_keys = (CONF_COVER_ENTITY_ID,) if control_type == "cover" else SCRIPT_FIELDS
        if user_input is not None:
            try:
                # Validate backend fields too, including their entity domains.
                schema = COVER_DEVICE_SCHEMA if control_type == "cover" else SCRIPT_DEVICE_SCHEMA
                schema({CONF_NAME: self._name, **user_input})
                options = validate_settings(
                    {key: user_input[key] for key in SETTINGS if key in user_input}
                )
            except TemplateError:
                errors[CONF_AVAILABILITY_TPL] = "invalid_template"
            except vol.Invalid:
                errors["base"] = "invalid_config"
            else:
                for key in source_keys:
                    entity = registry.async_get(user_input[key])
                    if entity is not None and entity.platform == DOMAIN:
                        errors[key] = "invalid_source"
                if not errors:
                    await self.async_set_unique_id(
                        control_type + ":" + ":".join(user_input[key] for key in source_keys)
                    )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=self._name,
                        data={
                            CONF_NAME: self._name,
                            **{key: user_input[key] for key in source_keys},
                        },
                        options=options,
                    )
        excluded = [
            entity.entity_id for entity in registry.entities.values() if entity.platform == DOMAIN
        ]
        fields = {
            vol.Required(key): selector.EntitySelector(
                {
                    "domain": "cover" if control_type == "cover" else "script",
                    "exclude_entities": excluded,
                }
            )
            for key in source_keys
        }
        fields.update(settings_schema(user_input or {}))
        return self.async_show_form(
            step_id=control_type, data_schema=vol.Schema(fields), errors=errors
        )


class CoverTimeBasedOptionsFlow(config_entries.OptionsFlow):
    """Edit calibration without recreating entity identities."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                options = validate_settings(user_input)
            except TemplateError:
                errors[CONF_AVAILABILITY_TPL] = "invalid_template"
            except vol.Invalid:
                errors["base"] = "invalid_config"
            else:
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(settings_schema(user_input or self.config_entry.options)),
            errors=errors,
        )
