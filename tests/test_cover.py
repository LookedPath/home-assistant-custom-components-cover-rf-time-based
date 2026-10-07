"""Exercise real Home Assistant entities, services and lifecycle cleanup."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol
from homeassistant.const import CONF_NAME
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.cover_rf_time_based.const import DOMAIN
from custom_components.cover_rf_time_based.cover import PLATFORM_SCHEMA, devices_from_config


async def setup_cover(hass, *, scripts=False, send_stop_at_ends=False, template=None):
    data = {CONF_NAME: "Test shutter"}
    if scripts:
        data.update(
            open_script_entity_id="script.open",
            close_script_entity_id="script.close",
            stop_script_entity_id="script.stop",
        )
    else:
        data["cover_entity_id"] = "cover.source"
    options = {
        "travelling_time_down": 25,
        "travelling_time_up": 25,
        "send_stop_at_ends": send_stop_at_ends,
    }
    if template is not None:
        options["availability_template"] = template
    entry = MockConfigEntry(domain=DOMAIN, data=data, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "cover", DOMAIN, "cover_rf_timebased_uuid_" + entry.entry_id
    )
    entity = hass.data["cover"].get_entity(entity_id)
    return entry, entity


def test_yaml_config_is_not_mutated():
    config = PLATFORM_SCHEMA(
        {
            "platform": DOMAIN,
            "devices": {"shutter": {"name": "Shutter", "cover_entity_id": "cover.source"}},
        }
    )
    before = deepcopy(config)
    first = devices_from_config(config)
    second = devices_from_config(config)
    assert config == before
    assert first[0].unique_id == second[0].unique_id == "cover_rf_timebased_uuid_shutter"


async def test_yaml_setup_and_services(hass):
    assert await async_setup_component(
        hass,
        "cover",
        {
            "cover": [
                {
                    "platform": DOMAIN,
                    "devices": {"shutter": {"name": "Shutter", "cover_entity_id": "cover.source"}},
                }
            ],
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get("cover.shutter") is not None
    assert hass.services.has_service(DOMAIN, "set_known_position")
    await hass.services.async_call(
        DOMAIN,
        "set_known_position",
        {"entity_id": "cover.shutter", "position": 50, "position_type": "current"},
        blocking=True,
    )
    assert hass.states.get("cover.shutter").attributes["current_position"] == 50


@pytest.mark.parametrize(
    "data",
    [
        {"position": -1},
        {"position": 101},
        {"position": 100.5},
        {"position": 50.5},
        {"position": 50, "position_type": "invalid"},
        {"action": "invalid"},
    ],
)
async def test_invalid_service_inputs(hass, data):
    _, entity = await setup_cover(hass)
    service = "set_known_action" if "action" in data else "set_known_position"
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, service, {"entity_id": entity.entity_id, **data}, blocking=True
        )
    assert entity.current_cover_position == 0


@pytest.mark.parametrize("scripts", [False, True])
async def test_partial_movement_stops_once_at_deadline(hass, scripts):
    _, entity = await setup_cover(hass, scripts=scripts)
    entity.tc.set_position(100)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.async_set_cover_position(position=50)
        assert call.await_count == 1
        entity.tc.time_set_from_outside = 112.3
        entity.auto_updater_hook(None)
        await hass.async_block_till_done()
        assert call.await_count == 1
        assert entity.is_closing
        entity.tc.time_set_from_outside = 112.5
        entity.auto_updater_hook(None)
        await hass.async_block_till_done()
        assert call.await_count == 2
        assert call.call_args.args[:2] == (
            ("homeassistant", "turn_on") if scripts else ("cover", "stop_cover")
        )
        assert entity._unsubscribe_auto_updater is None
        assert not entity.is_closing
        entity.auto_updater_hook(None)
        await hass.async_block_till_done()
        assert call.await_count == 2


@pytest.mark.parametrize("action", ["open", "close"])
async def test_external_actions_never_echo_motor_commands(hass, action):
    _, entity = await setup_cover(hass, send_stop_at_ends=True)
    entity.tc.set_position(50)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.set_known_action(action=action)
        assert entity._processing_known_position
        assert hass.states.get(entity.entity_id).state == (
            "opening" if action == "open" else "closing"
        )
        entity.tc.time_set_from_outside = 125
        entity.auto_updater_hook(None)
        await hass.async_block_till_done()
        call.assert_not_called()
        assert entity._unsubscribe_auto_updater is None


async def test_external_stop_and_current_position_publish_immediately(hass):
    _, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    await entity.set_known_action(action="open")
    entity.tc.time_set_from_outside = 105
    await entity.set_known_action(action="stop")
    assert hass.states.get(entity.entity_id).attributes["current_position"] == 20
    assert hass.states.get(entity.entity_id).state == "open"
    assert entity._unsubscribe_auto_updater is None
    await entity.set_known_position(position=60, position_type="current", confident=True)
    state = hass.states.get(entity.entity_id)
    assert state.attributes["current_position"] == 60
    assert state.attributes.get("assumed_state", False) is False


async def test_current_correction_preserves_moving_target_without_echo(hass):
    _, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.async_set_cover_position(position=80)
        entity.tc.time_set_from_outside = 105
        await entity.set_known_position(position=40, position_type="current")
        assert entity.current_cover_position == 40
        assert entity.tc.travel_to_position == 80
        entity.tc.time_set_from_outside = 115
        entity.auto_updater_hook(None)
        await hass.async_block_till_done()
        assert call.await_count == 1


async def test_sensor_target_finishes_without_stop(hass):
    _, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.set_known_position(position=50)
        entity.tc.time_set_from_outside = 112.5
        entity.auto_updater_hook(None)
        await hass.async_block_till_done()
        assert entity.current_cover_position == 50
        call.assert_not_called()


async def test_new_command_cancels_pending_completion(hass):
    _, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.async_set_cover_position(position=50)
        entity.tc.time_set_from_outside = 112.5
        entity.auto_updater_hook(None)
        task = entity._auto_stop_task
        await entity.async_open_cover()
        await hass.async_block_till_done()
        assert task.cancelled()
        assert call.await_count == 2
        assert entity.is_opening


async def test_target_equal_to_current_stops_moving_cover(hass):
    _, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.async_open_cover()
        entity.tc.time_set_from_outside = 105
        await entity.async_set_cover_position(position=20)
        assert not entity.tc.is_traveling()
        assert call.call_args.args[:2] == ("cover", "stop_cover")
        assert entity._unsubscribe_auto_updater is None


async def test_unload_cancels_timer_and_pending_completion(hass):
    entry, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    await entity.set_known_action(action="open")
    unsubscribe = Mock(wraps=entity._unsubscribe_auto_updater)
    entity._unsubscribe_auto_updater = unsubscribe
    assert await hass.config_entries.async_unload(entry.entry_id)
    unsubscribe.assert_called_once()
    assert entity._unsubscribe_auto_updater is None
    assert hass.data["cover"].get_entity(entity.entity_id) is None


async def test_availability_tracks_source_and_invalid_results(hass):
    hass.states.async_set("binary_sensor.bridge", "off")
    entry, entity = await setup_cover(hass, template="{{ is_state('binary_sensor.bridge', 'on') }}")
    assert not entity.available
    hass.states.async_set("binary_sensor.bridge", "on")
    await hass.async_block_till_done()
    assert entity.available
    assert hass.states.get(entity.entity_id).state != "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set("binary_sensor.bridge", "off")
    await hass.async_block_till_done()
    assert hass.data["cover"].get_entity(entity.entity_id) is None


@pytest.mark.parametrize("template", ["{{ 1 / 0 }}", "not a boolean", "False"])
async def test_bad_or_false_availability_is_unavailable(hass, template):
    _, entity = await setup_cover(hass, template=template)
    assert entity.available is False


async def test_position_and_confidence_restored(hass):
    from homeassistant.core import State

    mock_restore_cache(
        hass,
        [
            State(
                "cover.test_shutter", "open", {"current_position": 65, "unconfirmed_state": "False"}
            )
        ],
    )
    _, entity = await setup_cover(hass)
    assert entity.current_cover_position == 65
    assert entity.assumed_state is False


async def test_options_reload_preserves_entity_identity(hass):
    entry, entity = await setup_cover(hass)
    entity_id = entity.entity_id
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "travelling_time_up": 40}
    )
    await hass.async_block_till_done()
    new_entity = hass.data["cover"].get_entity(entity_id)
    assert new_entity is not entity
    assert new_entity.tc.travel_time_up == 40
    assert new_entity.unique_id == entity.unique_id


@pytest.mark.parametrize("send_stop_at_ends,expected_calls", [(False, 1), (True, 2)])
async def test_endpoint_stop_setting(hass, send_stop_at_ends, expected_calls):
    _, entity = await setup_cover(hass, send_stop_at_ends=send_stop_at_ends)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.async_open_cover()
        entity.tc.time_set_from_outside = 125
        entity.auto_updater_hook(None)
        await hass.async_block_till_done()
        assert call.await_count == expected_calls
        assert entity.current_cover_position == 100
        assert entity._unsubscribe_auto_updater is None


async def test_dispatch_failure_stops_estimation(hass):
    _, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    with patch(
        "homeassistant.core.ServiceRegistry.async_call", side_effect=RuntimeError("Cannot dispatch")
    ):
        with pytest.raises(RuntimeError, match="Cannot dispatch"):
            await entity.async_open_cover()
    assert not entity.tc.is_traveling()
    assert entity._unsubscribe_auto_updater is None


async def test_unload_cancels_queued_completion(hass):
    entry, entity = await setup_cover(hass)
    entity.tc.time_set_from_outside = 100
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as call:
        await entity.async_set_cover_position(position=50)
        entity.tc.time_set_from_outside = 112.5
        entity.auto_updater_hook(None)
        task = entity._auto_stop_task
        # Removal callbacks run before giving the pending task an opportunity to dispatch.
        await entity.async_remove()
        await hass.async_block_till_done()
        assert task.cancelled()
        assert call.await_count == 1
        assert entity._auto_stop_task is None
        assert await hass.config_entries.async_unload(entry.entry_id)
