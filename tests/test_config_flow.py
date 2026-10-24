"""Tests for the config and options flow."""

from typing import Any

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.degree_days.const import (
    CONF_BASE_TEMPERATURE,
    CONF_BASELINE_USAGE,
    CONF_CONSUMPTION_SENSOR,
    CONF_HEATING_LIMIT,
    CONF_IMPORT_STATISTICS,
    CONF_KNMI_STATION,
    CONF_KNMI_STATION_NAME,
    CONF_LOCATION,
    CONF_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SOURCE,
    CONF_WEIGHTING,
    CONF_YEAR_START_MONTH,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from .fake_weather import DE_BILT, SCHIPHOL, FakeKnmi, FakeOpenMeteo

SETTINGS = {
    CONF_BASE_TEMPERATURE: 18.0,
    CONF_HEATING_LIMIT: 18.0,
    CONF_WEIGHTING: "nl",
    CONF_BASELINE_USAGE: 0.3,
    CONF_YEAR_START_MONTH: "7",
    CONF_IMPORT_STATISTICS: False,
}


@pytest.fixture
def weather(aioclient_mock: AiohttpClientMocker) -> FakeKnmi:
    """Fake weather services, so entries created by the flows can load."""
    now = dt_util.utcnow()
    FakeOpenMeteo(final_until=now.date(), now=now).register(aioclient_mock)
    knmi = FakeKnmi(validated_until=now.date(), now=now)
    knmi.register(aioclient_mock)
    return knmi


def _field(result: dict, name: str) -> tuple[Any, Any]:
    """Return the schema marker and selector of a form field."""
    for marker, value in result["data_schema"].schema.items():
        if marker == name:
            return marker, value
    raise KeyError(name)


async def _start(hass: HomeAssistant, source: str, consumption: str | None) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    user_input: dict[str, Any] = {"name": "House", CONF_TEMPERATURE_SOURCE: source}
    if consumption:
        user_input[CONF_CONSUMPTION_SENSOR] = consumption
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


@pytest.mark.usefixtures("setup_integration_env", "weather")
async def test_sensor_flow_and_settings_options(hass: HomeAssistant) -> None:
    """Create an entry with a sensor and change the settings."""
    result = await _start(hass, "sensor", "sensor.gas")
    assert result["step_id"] == "source_sensor"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TEMPERATURE_SENSOR: "sensor.outdoor"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SETTINGS, CONF_HEATING_LIMIT: 20.0}
    )
    assert result["errors"] == {CONF_HEATING_LIMIT: "heating_limit_above_base"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "House"
    assert result["data"] == {CONF_CONSUMPTION_SENSOR: "sensor.gas"}
    assert result["options"][CONF_TEMPERATURE_SOURCE] == "sensor"
    assert result["options"][CONF_TEMPERATURE_SENSOR] == "sensor.outdoor"
    assert result["options"][CONF_YEAR_START_MONTH] == 7
    await hass.async_block_till_done()

    entry = result["result"]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**SETTINGS, CONF_BASE_TEMPERATURE: 17.0, CONF_HEATING_LIMIT: 15.5},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_HEATING_LIMIT] == 15.5
    # The temperature source is kept when the settings change.
    assert entry.options[CONF_TEMPERATURE_SENSOR] == "sensor.outdoor"


@pytest.mark.usefixtures("setup_integration_env", "weather")
async def test_open_meteo_flow(hass: HomeAssistant) -> None:
    """Open-Meteo only needs a location, defaulting to home."""
    hass.config.latitude, hass.config.longitude = 52.09, 5.12
    result = await _start(hass, "open_meteo", None)
    assert result["step_id"] == "source_open_meteo"
    marker, _ = _field(result, CONF_LOCATION)
    assert marker.default() == {"latitude": 52.09, "longitude": 5.12}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: {"latitude": 52.1, "longitude": 5.18}}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_LOCATION] == {"latitude": 52.1, "longitude": 5.18}
    await hass.async_block_till_done()

    # The same location again is a duplicate.
    result = await _start(hass, "open_meteo", None)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION: {"latitude": 52.1, "longitude": 5.18}}
    )
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("setup_integration_env")
async def test_knmi_flow(hass: HomeAssistant, weather: FakeKnmi) -> None:
    """A refused key is reported; stations are sorted by distance."""
    hass.config.latitude, hass.config.longitude = 52.09, 5.12  # near De Bilt
    result = await _start(hass, "knmi", "sensor.gas")
    assert result["step_id"] == "source_knmi"

    weather.refuse = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "wrong"}
    )
    assert result["errors"] == {CONF_API_KEY: "invalid_auth"}
    weather.refuse = False

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: " secret "}
    )
    assert result["step_id"] == "knmi_station"
    marker, station = _field(result, CONF_KNMI_STATION)
    assert marker.default() == DE_BILT
    assert [option["label"] for option in station.config["options"]] == [
        "De Bilt (4 km)",
        "Schiphol (34 km)",
    ]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_KNMI_STATION: DE_BILT}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_API_KEY] == "secret"
    assert result["options"][CONF_KNMI_STATION] == DE_BILT
    assert result["options"][CONF_KNMI_STATION_NAME] == "De Bilt"


@pytest.mark.usefixtures("setup_integration_env", "weather")
async def test_switch_source_in_options(hass: HomeAssistant) -> None:
    """Switch from a sensor to KNMI and back to Open-Meteo."""
    result = await _start(hass, "sensor", "sensor.gas")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TEMPERATURE_SENSOR: "sensor.outdoor"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.unique_id == "sensor.outdoor_sensor.gas"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "temperature_source"}
    )
    assert result["step_id"] == "temperature_source"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TEMPERATURE_SOURCE: "knmi"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_API_KEY: "secret"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_KNMI_STATION: SCHIPHOL}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_TEMPERATURE_SOURCE] == "knmi"
    assert entry.options[CONF_KNMI_STATION_NAME] == "Schiphol"
    assert CONF_TEMPERATURE_SENSOR not in entry.options
    # Calculation settings are kept.
    assert entry.options[CONF_BASELINE_USAGE] == 0.3
    assert entry.unique_id == f"knmi_{SCHIPHOL}_sensor.gas"
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert entry.runtime_data.remote is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "temperature_source"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TEMPERATURE_SOURCE: "open_meteo"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LOCATION: {"latitude": 51.5, "longitude": 4.5}}
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_TEMPERATURE_SOURCE] == "open_meteo"
    assert CONF_API_KEY not in entry.options
    assert entry.state is config_entries.ConfigEntryState.LOADED


@pytest.mark.usefixtures("setup_integration_env", "weather")
async def test_reauth(hass: HomeAssistant) -> None:
    """A new KNMI key can be entered."""
    result = await _start(hass, "knmi", None)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "old"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_KNMI_STATION: DE_BILT}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    entry = result["result"]
    await hass.async_block_till_done()

    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    [flow] = hass.config_entries.flow.async_progress()
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "new"}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.options[CONF_API_KEY] == "new"
    assert entry.options[CONF_KNMI_STATION] == DE_BILT


@pytest.mark.usefixtures("setup_integration_env", "weather")
async def test_duplicate_aborts(hass: HomeAssistant) -> None:
    """The same sensors cannot be added twice; other combinations can."""

    async def add(temperature: str, consumption: str | None) -> dict:
        result = await _start(hass, "sensor", consumption)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TEMPERATURE_SENSOR: temperature}
        )
        if result["type"] is FlowResultType.FORM:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], SETTINGS
            )
        await hass.async_block_till_done()
        return result

    assert (await add("sensor.outdoor", None))["type"] is FlowResultType.CREATE_ENTRY
    assert (await add("sensor.outdoor", None))["reason"] == "already_configured"
    assert (await add("sensor.outdoor", "sensor.gas"))[
        "type"
    ] is FlowResultType.CREATE_ENTRY
    assert (await add("sensor.outdoor", "sensor.gas"))["reason"] == "already_configured"
