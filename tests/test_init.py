"""Integration tests using a real recorder with imported statistics."""

from datetime import datetime, timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

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
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

TEMP = "sensor.outdoor_temperature"
GAS = "sensor.gas_meter"


def _import_history(hass: HomeAssistant, start: datetime, days: int) -> None:
    """Import hourly statistics: 8 °C and 0.1 m³/h on even days, 13 °C and 0.05 on odd."""
    temps: list[StatisticData] = []
    gas: list[StatisticData] = []
    total = 0.0
    for hour in range(days * 24):
        ts = start + timedelta(hours=hour)
        even = (hour // 24) % 2 == 0
        temp = 8.0 if even else 13.0
        total += 0.1 if even else 0.05
        temps.append(StatisticData(start=ts, mean=temp, min=temp, max=temp))
        gas.append(StatisticData(start=ts, state=total, sum=total))
    async_import_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.ARITHMETIC,
            has_sum=False,
            name=None,
            source="recorder",
            statistic_id=TEMP,
            unit_class="temperature",
            unit_of_measurement="°C",
        ),
        temps,
    )
    async_import_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=GAS,
            unit_class="volume",
            unit_of_measurement="m³",
        ),
        gas,
    )


@pytest.mark.usefixtures("setup_integration_env")
async def test_setup_and_services(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Set up from history and check sensors, services and statistics."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(
        dt_util.as_utc(
            datetime(2025, 1, 21, 12, 0, tzinfo=dt_util.get_default_time_zone())
        )
    )
    hass.states.async_set(
        TEMP, "8.0", {"unit_of_measurement": "°C", "state_class": "measurement"}
    )
    hass.states.async_set(
        GAS, "100", {"unit_of_measurement": "m³", "device_class": "gas"}
    )

    start = dt_util.as_utc(datetime(2025, 1, 1, tzinfo=dt_util.get_default_time_zone()))
    _import_history(hass, start, 20)  # 1..20 January
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        data={CONF_TEMPERATURE_SENSOR: TEMP, CONF_CONSUMPTION_SENSOR: GAS},
        options={
            CONF_BASE_TEMPERATURE: 18.0,
            CONF_HEATING_LIMIT: 18.0,
            CONF_WEIGHTING: "nl",
            CONF_BASELINE_USAGE: 0.0,
            CONF_YEAR_START_MONTH: 1,
            CONF_IMPORT_STATISTICS: True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # 20 January is the 20th day -> odd index (19) -> 13 °C, 5 degree days.
    assert (
        hass.states.get("sensor.home_mean_outdoor_temperature_yesterday").state
        == "13.0"
    )
    assert hass.states.get("sensor.home_degree_days_yesterday").state == "5.0"
    assert hass.states.get("sensor.home_weighted_degree_days_yesterday").state == "5.5"
    assert float(hass.states.get("sensor.home_usage_yesterday").state) == pytest.approx(
        1.2
    )
    # Today has no statistics yet, so the current state (8 °C) is used.
    assert hass.states.get("sensor.home_weighted_degree_days_today").state == "11.0"
    # 10 days with 10 DD and 10 days with 5 DD -> 150 DD * 1.1, plus today.
    assert float(
        hass.states.get("sensor.home_weighted_degree_days_this_year").state
    ) == pytest.approx(176.0)
    assert float(hass.states.get("sensor.home_usage_this_year").state) == pytest.approx(
        36.0
    )
    per_dd = hass.states.get("sensor.home_usage_per_weighted_degree_day_this_year")
    assert float(per_dd.state) == pytest.approx(36.0 / 176.0, abs=1e-4)
    assert per_dd.attributes["unit_of_measurement"] == "m³/°C·d"

    response = await hass.services.async_call(
        DOMAIN,
        "compare_periods",
        {
            "config_entry_id": entry.entry_id,
            "period_a_start": "2025-01-01",
            "period_a_end": "2025-01-10",
            "period_b_start": "2025-01-11",
            "period_b_end": "2025-01-20",
        },
        blocking=True,
        return_response=True,
    )
    assert response["unit"] == "m³"
    assert response["period_a"]["weighted_degree_days"] == pytest.approx(82.5)
    assert response["period_a"]["usage"] == pytest.approx(18.0)
    assert response["comparison"]["weather_corrected_difference_pct"] == pytest.approx(
        0.0, abs=0.01
    )

    response = await hass.services.async_call(
        DOMAIN,
        "get_daily_data",
        {
            "config_entry_id": entry.entry_id,
            "start": "2025-01-01",
            "end": "2025-01-20",
            "group_by": "week",
        },
        blocking=True,
        return_response=True,
    )
    assert response["periods"][0]["start"] == "2025-01-01"
    assert response["periods"][0]["end"] == "2025-01-05"

    await async_wait_recording_done(hass)
    stat_id = f"{DOMAIN}:{entry.entry_id.lower()}_weighted_degree_days"
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {stat_id},
        "month",
        None,
        {"change"},
    )
    assert stats[stat_id][0]["change"] == pytest.approx(165.0)

    # Unchanged days are not written again on the next update.
    with patch(
        "custom_components.degree_days.coordinator.async_add_external_statistics"
    ) as add_statistics:
        await entry.runtime_data.async_refresh()
    add_statistics.assert_not_called()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_daily_data",
            {
                "config_entry_id": entry.entry_id,
                "start": "1990-01-01",
                "end": "2025-01-20",
            },
            blocking=True,
            return_response=True,
        )

    # Removing the entry removes its statistics.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await async_wait_recording_done(hass)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        {stat_id},
        "month",
        None,
        {"change"},
    )
    assert stat_id not in stats


@pytest.mark.usefixtures("setup_integration_env")
async def test_first_day_of_year(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """On 1 January the year-on-year comparison is empty, not reversed."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = dt_util.get_default_time_zone()
    freezer.move_to(dt_util.as_utc(datetime(2025, 1, 1, 12, 0, tzinfo=tz)))
    hass.states.async_set(TEMP, "8.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(GAS, "100", {"unit_of_measurement": "m³"})
    _import_history(hass, dt_util.as_utc(datetime(2024, 12, 1, tzinfo=tz)), 31)
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        data={CONF_TEMPERATURE_SENSOR: TEMP, CONF_CONSUMPTION_SENSOR: GAS},
        options={CONF_YEAR_START_MONTH: 1, CONF_IMPORT_STATISTICS: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    data = entry.runtime_data.data
    assert data.year_to_yesterday.days == 0
    assert data.year.days == 1
    assert data.previous_year.days == 366
    assert data.previous_year.days_with_usage == 31
    assert (
        hass.states.get("sensor.home_weather_corrected_difference_with_last_year").state
        == "unknown"
    )


@pytest.mark.usefixtures("setup_integration_env")
async def test_unit_when_meter_not_available_yet(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Usage sensors get the statistics unit when the meter has no state yet."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = dt_util.get_default_time_zone()
    freezer.move_to(dt_util.as_utc(datetime(2025, 1, 21, 12, 0, tzinfo=tz)))
    hass.states.async_set(TEMP, "8.0", {"unit_of_measurement": "°C"})
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

    assert hass.states.get(GAS) is None
    state = hass.states.get("sensor.home_usage_this_year")
    assert state.attributes["unit_of_measurement"] == "m³"
    state = hass.states.get("sensor.home_usage_per_weighted_degree_day_last_30_days")
    assert state.attributes["unit_of_measurement"] == "m³/°C·d"
