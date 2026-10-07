"""Configuration and service validation."""

import voluptuous as vol
from homeassistant.components.cover import ATTR_POSITION, DEVICE_CLASSES_SCHEMA
from homeassistant.const import CONF_DEVICE_CLASS, CONF_NAME
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_ACTION,
    ATTR_CONFIDENT,
    ATTR_POSITION_TYPE,
    ATTR_POSITION_TYPE_CURRENT,
    ATTR_POSITION_TYPE_TARGET,
    CONF_ALIASES,
    CONF_ALWAYS_CONFIDENT,
    CONF_AVAILABILITY_TPL,
    CONF_CLOSE_SCRIPT_ENTITY_ID,
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
)


def integer(value):
    """Accept integer-valued selectors and strings without silently rounding."""
    converted = vol.Coerce(int)(value)
    if isinstance(value, bool) or isinstance(value, float) and converted != value:
        raise vol.Invalid("Expected an integer")
    return converted


TRAVEL_TIME = vol.All(integer, vol.Range(min=1))
POSITION = vol.All(integer, vol.Range(min=0, max=100))
POSITION_TYPE = vol.In([ATTR_POSITION_TYPE_TARGET, ATTR_POSITION_TYPE_CURRENT])
ACTION = vol.In(["open", "close", "stop"])

BASE_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_ALIASES, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_TRAVELLING_TIME_DOWN, default=DEFAULT_TRAVEL_TIME): TRAVEL_TIME,
        vol.Optional(CONF_TRAVELLING_TIME_UP, default=DEFAULT_TRAVEL_TIME): TRAVEL_TIME,
        vol.Optional(CONF_SEND_STOP_AT_ENDS, default=DEFAULT_SEND_STOP_AT_ENDS): cv.boolean,
        vol.Optional(CONF_ALWAYS_CONFIDENT, default=DEFAULT_ALWAYS_CONFIDENT): cv.boolean,
        vol.Optional(CONF_DEVICE_CLASS, default=DEFAULT_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
        vol.Optional(CONF_AVAILABILITY_TPL): cv.template,
    }
)
SCRIPT_DEVICE_SCHEMA = BASE_DEVICE_SCHEMA.extend(
    {
        vol.Required(CONF_OPEN_SCRIPT_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_CLOSE_SCRIPT_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_STOP_SCRIPT_ENTITY_ID): cv.entity_id,
    }
)
COVER_DEVICE_SCHEMA = BASE_DEVICE_SCHEMA.extend(
    {
        vol.Required(CONF_COVER_ENTITY_ID): cv.entity_domain("cover"),
    }
)

# Dicts are used by Home Assistant's platform service helper, which supplies targets.
POSITION_FIELDS = {
    vol.Required(ATTR_POSITION): POSITION,
    vol.Optional(ATTR_CONFIDENT, default=False): cv.boolean,
    vol.Optional(ATTR_POSITION_TYPE, default=ATTR_POSITION_TYPE_TARGET): POSITION_TYPE,
}
ACTION_FIELDS = {vol.Required(ATTR_ACTION): ACTION}
