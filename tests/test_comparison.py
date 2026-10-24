"""Tests for the period comparison entities."""

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.degree_days.const import (
    CONF_CONSUMPTION_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .test_init import GAS, TEMP, _import_history

MAIN = "sensor.home_comparison_weather_corrected_difference"


async def _set_date(hass: HomeAssistant, key: str, value: str) -> None:
    await hass.services.async_call(
        "date",
        "set_value",
        {"entity_id": f"date.home_{key}", "date": value},
        blocking=True,
    )


@pytest.mark.usefixtures("setup_integration_env")
async def test_compare_periods_entities(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Choose periods with the date and select entities."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = dt_util.get_default_time_zone()
    freezer.move_to(dt_util.as_utc(datetime(2025, 1, 21, 12, 0, tzinfo=tz)))
    hass.states.async_set(TEMP, "8.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(GAS, "100", {"unit_of_measurement": "m³"})
    _import_history(hass, dt_util.as_utc(datetime(2025, 1, 1, tzinfo=tz)), 20)
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        data={CONF_TEMPERATURE_SENSOR: TEMP, CONF_CONSUMPTION_SENSOR: GAS},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Default preset: the 4 weeks before them have no data yet.
    assert hass.states.get("select.home_comparison").state == "last_4_weeks"
    assert hass.states.get("date.home_period_b_end").state == "2025-01-20"
    assert hass.states.get("date.home_period_b_start").state == "2024-12-24"
    state = hass.states.get(MAIN)
    assert state.state == "unknown"
    assert state.attributes["problem"] == "no_data"

    # Changing a date switches to custom dates.
    await _set_date(hass, "period_a_start", "2025-01-01")
    await _set_date(hass, "period_a_end", "2025-01-10")
    await _set_date(hass, "period_b_start", "2025-01-11")
    await _set_date(hass, "period_b_end", "2025-01-20")
    assert hass.states.get("select.home_comparison").state == "custom"
    state = hass.states.get(MAIN)
    assert float(state.state) == pytest.approx(0.0, abs=0.05)
    assert state.attributes["period_a"] == "2025-01-01 – 2025-01-10"
    assert state.attributes["problem"] is None
    assert float(
        hass.states.get("sensor.home_comparison_weighted_degree_days_a").state
    ) == pytest.approx(82.5)
    assert float(
        hass.states.get("sensor.home_comparison_usage_a").state
    ) == pytest.approx(18.0)
    assert hass.states.get("sensor.home_comparison_usage_difference").state == "0.0"

    # An end before the start is reported instead of giving a number.
    await _set_date(hass, "period_a_end", "2024-12-01")
    state = hass.states.get(MAIN)
    assert state.state == "unknown"
    assert state.attributes["problem"] == "end_before_start"
    await _set_date(hass, "period_a_end", "2025-01-10")

    # A preset replaces the dates; going back to custom restores them.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.home_comparison", "option": "month_vs_last_year"},
        blocking=True,
    )
    assert hass.states.get("date.home_period_a_start").state == "2024-01-01"
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.home_comparison", "option": "custom"},
        blocking=True,
    )
    assert hass.states.get("date.home_period_a_end").state == "2025-01-10"

    # The settings survive a reload.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.home_comparison").state == "custom"
    assert hass.states.get("date.home_period_b_start").state == "2025-01-11"
    assert float(hass.states.get(MAIN).state) == pytest.approx(0.0, abs=0.05)
