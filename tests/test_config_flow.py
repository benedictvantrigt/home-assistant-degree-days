"""Tests for the config and options flow."""

import pytest

from custom_components.degree_days.const import (
    CONF_BASE_TEMPERATURE,
    CONF_BASELINE_USAGE,
    CONF_CONSUMPTION_SENSOR,
    CONF_HEATING_LIMIT,
    CONF_IMPORT_STATISTICS,
    CONF_TEMPERATURE_SENSOR,
    CONF_WEIGHTING,
    CONF_YEAR_START_MONTH,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

SETTINGS = {
    CONF_BASE_TEMPERATURE: 18.0,
    CONF_HEATING_LIMIT: 18.0,
    CONF_WEIGHTING: "nl",
    CONF_BASELINE_USAGE: 0.3,
    CONF_YEAR_START_MONTH: "7",
    CONF_IMPORT_STATISTICS: False,
}


@pytest.mark.usefixtures("setup_integration_env")
async def test_user_flow(hass: HomeAssistant) -> None:
    """Create an entry through the UI."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "House",
            CONF_TEMPERATURE_SENSOR: "sensor.outdoor",
            CONF_CONSUMPTION_SENSOR: "sensor.gas",
        },
    )
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SETTINGS, CONF_HEATING_LIMIT: 20.0}
    )
    assert result["errors"] == {CONF_HEATING_LIMIT: "heating_limit_above_base"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "House"
    assert result["data"] == {
        CONF_TEMPERATURE_SENSOR: "sensor.outdoor",
        CONF_CONSUMPTION_SENSOR: "sensor.gas",
    }
    assert result["options"][CONF_YEAR_START_MONTH] == 7
    await hass.async_block_till_done()

    entry = result["result"]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**SETTINGS, CONF_BASE_TEMPERATURE: 17.0, CONF_HEATING_LIMIT: 15.5},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_HEATING_LIMIT] == 15.5
