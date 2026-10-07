"""UI flows create usable entries and validate calibration settings."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cover_rf_time_based.const import DOMAIN

SETTINGS = {
    "travelling_time_up": 25,
    "travelling_time_down": 30,
    "send_stop_at_ends": False,
    "always_confident": False,
    "device_class": "shutter",
}


@pytest.mark.parametrize(
    "control_type,source",
    [
        ("cover", {"cover_entity_id": "cover.source"}),
        (
            "scripts",
            {
                "open_script_entity_id": "script.open",
                "close_script_entity_id": "script.close",
                "stop_script_entity_id": "script.stop",
            },
        ),
    ],
)
async def test_ui_setup(hass, control_type, source):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Bedroom", "control_type": control_type}
    )
    assert result["step_id"] == control_type
    with patch("custom_components.cover_rf_time_based.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**source, **SETTINGS}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {"name": "Bedroom", **source}
    assert result["options"] == SETTINGS


async def test_duplicate_source_is_rejected(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="cover:cover.source",
        data={"name": "Existing", "cover_entity_id": "cover.source"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"name": "Duplicate", "control_type": "cover"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cover_entity_id": "cover.source", **SETTINGS}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_wrapping_another_time_based_cover_is_rejected(hass):
    registry = er.async_get(hass)
    source = registry.async_get_or_create("cover", DOMAIN, "source", suggested_object_id="source")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"name": "Loop", "control_type": "cover"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"cover_entity_id": source.entity_id, **SETTINGS}
        )


async def test_bad_template_can_be_corrected(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"name": "Bedroom", "control_type": "cover"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"cover_entity_id": "cover.source", **SETTINGS, "availability_template": "{{ broken"},
        )
    with patch("custom_components.cover_rf_time_based.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"cover_entity_id": "cover.source", **SETTINGS, "availability_template": "{{ true }}"},
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"]["availability_template"] == "{{ true }}"


async def test_options_update_and_clear_template(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Bedroom", "cover_entity_id": "cover.source"},
        options={**SETTINGS, "availability_template": "{{ false }}"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    with patch("custom_components.cover_rf_time_based.async_update_options", return_value=None):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**SETTINGS, "travelling_time_up": 40, "availability_template": ""}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["travelling_time_up"] == 40
    assert "availability_template" not in entry.options


async def test_invalid_options(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"name": "Bedroom", "cover_entity_id": "cover.source"}, options=SETTINGS
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {**SETTINGS, "availability_template": "{{ broken"}
        )
