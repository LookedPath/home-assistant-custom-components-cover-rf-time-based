"""Set up the Cover Time Based integration and its observation services."""

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN, SERVICE_SET_KNOWN_ACTION, SERVICE_SET_KNOWN_POSITION
from .schema import ACTION_FIELDS, POSITION_FIELDS

PLATFORMS = [Platform.COVER]
CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    """Register services for both YAML and UI entities."""
    if not hasattr(service, "async_register_platform_entity_service"):
        # Home Assistant 2025.7 registers these through the entity platform.
        return True
    for service_name, schema in (
        (SERVICE_SET_KNOWN_POSITION, POSITION_FIELDS),
        (SERVICE_SET_KNOWN_ACTION, ACTION_FIELDS),
    ):
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            service_name,
            entity_domain=Platform.COVER,
            schema=schema,
            func=service_name,
        )
    return True


async def async_setup_entry(hass, entry):
    """Forward a UI entry to the cover platform."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(hass, entry):
    """Unload entities and their subscriptions."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass, entry):
    """Apply changed calibration and display settings."""
    await hass.config_entries.async_reload(entry.entry_id)
