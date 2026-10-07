"""Time-based covers controlled by scripts or an existing cover entity."""

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    PLATFORM_SCHEMA,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
)
from homeassistant.core import callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform, service
from homeassistant.helpers.event import (
    TrackTemplate,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    ATTR_ACTION,
    ATTR_CONFIDENT,
    ATTR_POSITION_TYPE,
    ATTR_POSITION_TYPE_TARGET,
    ATTR_UNCONFIRMED_STATE,
    CONF_ALWAYS_CONFIDENT,
    CONF_AVAILABILITY_TPL,
    CONF_CLOSE_SCRIPT_ENTITY_ID,
    CONF_COVER_ENTITY_ID,
    CONF_DEVICES,
    CONF_OPEN_SCRIPT_ENTITY_ID,
    CONF_SEND_STOP_AT_ENDS,
    CONF_STOP_SCRIPT_ENTITY_ID,
    CONF_TRAVELLING_TIME_DOWN,
    CONF_TRAVELLING_TIME_UP,
    SERVICE_SET_KNOWN_ACTION,
    SERVICE_SET_KNOWN_POSITION,
)
from .schema import (
    ACTION,
    ACTION_FIELDS,
    COVER_DEVICE_SCHEMA,
    POSITION,
    POSITION_FIELDS,
    POSITION_TYPE,
    SCRIPT_DEVICE_SCHEMA,
)
from .travelcalculator import TravelCalculator, TravelStatus

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_DEVICES, default={}): vol.Schema(
            {
                cv.string: vol.Any(SCRIPT_DEVICE_SCHEMA, COVER_DEVICE_SCHEMA),
            }
        ),
    }
)


def devices_from_config(domain_config):
    """Build devices without modifying the supplied configuration."""
    return [
        CoverTimeBased(
            device_id,
            config[CONF_NAME],
            config[CONF_TRAVELLING_TIME_DOWN],
            config[CONF_TRAVELLING_TIME_UP],
            config.get(CONF_OPEN_SCRIPT_ENTITY_ID),
            config.get(CONF_CLOSE_SCRIPT_ENTITY_ID),
            config.get(CONF_STOP_SCRIPT_ENTITY_ID),
            config.get(CONF_COVER_ENTITY_ID),
            config[CONF_SEND_STOP_AT_ENDS],
            config[CONF_ALWAYS_CONFIDENT],
            config[CONF_DEVICE_CLASS],
            config.get(CONF_AVAILABILITY_TPL),
        )
        for device_id, config in domain_config[CONF_DEVICES].items()
    ]


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up existing YAML configurations."""
    async_add_entities(devices_from_config(config))
    _register_legacy_services()


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up one UI-configured cover using the same validation as YAML."""
    config = {**entry.data, **entry.options}
    schema = COVER_DEVICE_SCHEMA if CONF_COVER_ENTITY_ID in config else SCRIPT_DEVICE_SCHEMA
    async_add_entities(devices_from_config({CONF_DEVICES: {entry.entry_id: schema(config)}}))
    _register_legacy_services()


@callback
def _register_legacy_services():
    """Use the old registration API only on versions that require it."""
    if hasattr(service, "async_register_platform_entity_service"):
        return
    platform = entity_platform.async_get_current_platform()
    for service_name, fields in (
        (SERVICE_SET_KNOWN_POSITION, POSITION_FIELDS),
        (SERVICE_SET_KNOWN_ACTION, ACTION_FIELDS),
    ):
        platform.async_register_entity_service(
            service_name, cv.make_entity_service_schema(fields), service_name
        )


class CoverTimeBased(CoverEntity, RestoreEntity):
    """Estimate movement while keeping external observations command-free."""

    _attr_should_poll = False
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        device_id,
        name,
        travel_time_down,
        travel_time_up,
        open_script_entity_id,
        close_script_entity_id,
        stop_script_entity_id,
        cover_entity_id,
        send_stop_at_ends,
        always_confident,
        device_class,
        availability_template,
    ):
        self._name = name or device_id
        self._unique_id = device_id
        self._travel_time_down = travel_time_down
        self._travel_time_up = travel_time_up
        self._open_script_entity_id = open_script_entity_id
        self._close_script_entity_id = close_script_entity_id
        self._stop_script_entity_id = stop_script_entity_id
        self._cover_entity_id = cover_entity_id
        self._send_stop_at_ends = send_stop_at_ends
        self._always_confident = always_confident
        self._device_class = device_class
        self._assume_uncertain_position = not always_confident
        self._target_position = 0
        self._processing_known_position = False
        self._movement_pending_completion = False
        self._availability_template = availability_template
        self._attr_available = availability_template is None
        self._unsubscribe_auto_updater = None
        self._auto_stop_task = None
        self.tc = TravelCalculator(travel_time_down, travel_time_up)

    async def async_added_to_hass(self):
        """Restore position and subscribe to availability changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self.stop_auto_updater)
        self.async_on_remove(self._cancel_auto_stop)
        old_state = await self.async_get_last_state()
        if old_state is not None:
            position = old_state.attributes.get(ATTR_CURRENT_POSITION)
            if position is not None:
                try:
                    self.tc.set_position(POSITION(position))
                    self._target_position = self.tc.current_position()
                except (vol.Invalid, TypeError, ValueError):
                    _LOGGER.warning(
                        "Ignoring invalid restored position for %s: %s", self.name, position
                    )
            if not self._always_confident:
                uncertain = old_state.attributes.get(ATTR_UNCONFIRMED_STATE)
                if uncertain is not None:
                    self._assume_uncertain_position = str(uncertain).lower() == "true"
        if self._availability_template is not None:
            self._availability_template.hass = self.hass
            tracker = async_track_template_result(
                self.hass,
                [TrackTemplate(self._availability_template, None)],
                self._handle_availability_result,
            )
            self.async_on_remove(tracker.async_remove)
            tracker.async_refresh()

    @callback
    def _handle_availability_result(self, event, updates):
        """Keep availability a boolean and fail closed on template errors."""
        result = updates[0].result
        try:
            if isinstance(result, TemplateError):
                raise result
            self._attr_available = cv.boolean(result)
        except (TemplateError, vol.Invalid):
            self._attr_available = False
            _LOGGER.warning("Invalid availability template result for %s: %s", self.name, result)
        self.async_write_ha_state()

    @callback
    def _cancel_auto_stop(self):
        """Discard a pending completion when a new action supersedes it."""
        task = self._auto_stop_task
        self._auto_stop_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _handle_stop(self):
        """Stop the estimate and any outstanding timer regardless of rounding."""
        self._cancel_auto_stop()
        self._movement_pending_completion = False
        self.tc.stop()
        self._target_position = self.tc.current_position()
        self.stop_auto_updater()

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        # Preserve the existing entity registry identity for YAML users.
        return "cover_rf_timebased_uuid_" + self._unique_id

    @property
    def device_class(self):
        return self._device_class

    @property
    def extra_state_attributes(self):
        return {
            CONF_TRAVELLING_TIME_DOWN: self._travel_time_down,
            CONF_TRAVELLING_TIME_UP: self._travel_time_up,
            ATTR_UNCONFIRMED_STATE: str(self._assume_uncertain_position),
        }

    @property
    def current_cover_position(self):
        return self.tc.current_position()

    @property
    def is_opening(self):
        return self.tc.is_traveling() and self.tc.travel_direction == TravelStatus.DIRECTION_UP

    @property
    def is_closing(self):
        return self.tc.is_traveling() and self.tc.travel_direction == TravelStatus.DIRECTION_DOWN

    @property
    def is_closed(self):
        return self.tc.is_closed()

    @property
    def assumed_state(self):
        return self._assume_uncertain_position

    async def async_set_cover_position(self, **kwargs):
        """Move to an absolute position."""
        await self.set_position(POSITION(kwargs[ATTR_POSITION]))

    async def async_close_cover(self, **kwargs):
        """Close the cover, allowing repeated RF commands at endpoints."""
        await self._start_command(0, SERVICE_CLOSE_COVER)

    async def async_open_cover(self, **kwargs):
        """Open the cover, allowing repeated RF commands at endpoints."""
        await self._start_command(100, SERVICE_OPEN_COVER)

    async def _start_command(self, position, command):
        self._cancel_auto_stop()
        self._movement_pending_completion = True
        self._target_position = position
        self.tc.start_travel(position)
        self.start_auto_updater()
        await self._async_handle_command(command)

    async def async_stop_cover(self, **kwargs):
        """Stop both the estimate and physical cover."""
        self._handle_stop()
        await self._async_handle_command(SERVICE_STOP_COVER)

    async def set_position(self, position):
        """Move to a target, or stop if the requested position is already current."""
        position = POSITION(position)
        current_position = self.tc.current_position()
        if position == current_position:
            if self.tc.is_traveling():
                await self.async_stop_cover()
            return
        command = SERVICE_CLOSE_COVER if position < current_position else SERVICE_OPEN_COVER
        await self._start_command(position, command)

    def start_auto_updater(self):
        """Refresh displayed position while moving."""
        if self._unsubscribe_auto_updater is None:
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass,
                self.auto_updater_hook,
                timedelta(seconds=0.1),
            )

    @callback
    def auto_updater_hook(self, now):
        """Publish progress and schedule completion exactly once."""
        self.async_write_ha_state()
        if self.position_reached():
            self.stop_auto_updater()
            if self._movement_pending_completion and self._auto_stop_task is None:
                self._auto_stop_task = self.hass.async_create_task(
                    self.auto_stop_if_necessary(), eager_start=False
                )

    @callback
    def stop_auto_updater(self):
        """Release the movement timer."""
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    def position_reached(self):
        return self.tc.position_reached()

    async def set_known_action(self, **kwargs):
        """Observe a remote command without sending any command back."""
        action = ACTION(kwargs[ATTR_ACTION])
        self._cancel_auto_stop()
        self._processing_known_position = True
        self._assume_uncertain_position = not self._always_confident
        if action == "stop":
            self._handle_stop()
        else:
            self._target_position = 100 if action == "open" else 0
            self._movement_pending_completion = True
            self.tc.start_travel(self._target_position)
            self.start_auto_updater()
        self.async_write_ha_state()

    async def set_known_position(self, **kwargs):
        """Observe a position, preserving the existing target when still moving."""
        position = POSITION(kwargs[ATTR_POSITION])
        position_type = POSITION_TYPE(kwargs.get(ATTR_POSITION_TYPE, ATTR_POSITION_TYPE_TARGET))
        was_traveling = self.tc.is_traveling()
        self._cancel_auto_stop()
        self._assume_uncertain_position = not (
            kwargs.get(ATTR_CONFIDENT, False) or self._always_confident
        )
        self._processing_known_position = True
        if position_type == ATTR_POSITION_TYPE_TARGET:
            self._target_position = position
            self.tc.start_travel(position)
        else:
            self.tc.set_position(position)
            if was_traveling:
                self.tc.start_travel(self._target_position)
            else:
                self._target_position = position
        self._movement_pending_completion = self.tc.is_traveling()
        if self._movement_pending_completion:
            self.start_auto_updater()
        else:
            self.stop_auto_updater()
        self.async_write_ha_state()

    async def auto_stop_if_necessary(self):
        """Finish travel and send at most one stop, only for HA-owned movement."""
        try:
            if not self._movement_pending_completion or not self.position_reached():
                return
            self._movement_pending_completion = False
            self.stop_auto_updater()
            current_position = self.tc.current_position()
            self.tc.stop()
            if not self._processing_known_position and (
                0 < current_position < 100 or self._send_stop_at_ends
            ):
                await self._async_handle_command(SERVICE_STOP_COVER)
            else:
                self.async_write_ha_state()
        finally:
            if self._auto_stop_task is asyncio.current_task():
                self._auto_stop_task = None

    async def _async_handle_command(self, command):
        """Dispatch a command and publish the new estimate."""
        self._assume_uncertain_position = not self._always_confident
        self._processing_known_position = False
        if self._cover_entity_id is not None:
            domain, service, entity_id = "cover", command, self._cover_entity_id
        else:
            scripts = {
                SERVICE_OPEN_COVER: self._open_script_entity_id,
                SERVICE_CLOSE_COVER: self._close_script_entity_id,
                SERVICE_STOP_COVER: self._stop_script_entity_id,
            }
            domain, service, entity_id = "homeassistant", "turn_on", scripts[command]
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, False)
        except Exception:
            self._handle_stop()
            self.async_write_ha_state()
            raise
        self.async_write_ha_state()
