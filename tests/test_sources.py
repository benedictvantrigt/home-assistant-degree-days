"""Tests for the Open-Meteo and KNMI temperature sources."""

from datetime import date, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.degree_days.const import (
    CONF_CONSUMPTION_SENSOR,
    CONF_HISTORY_START,
    CONF_KNMI_STATION,
    CONF_LOCATION,
    CONF_TEMPERATURE_SOURCE,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .fake_weather import (
    DE_BILT,
    RECENT_TEMPERATURE,
    TZ,
    FakeKnmi,
    FakeOpenMeteo,
    final_temperature,
)
from .test_init import GAS, _import_history

NOW = datetime(2025, 1, 21, 12, 0, tzinfo=TZ)
TODAY = NOW.date()


def _state(hass: HomeAssistant, key: str) -> str:
    return hass.states.get(f"sensor.home_{key}").state


async def _setup(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        data={CONF_CONSUMPTION_SENSOR: GAS},
        options=options,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def history(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Gas meter history for 1..20 January 2025 and the clock on 21 January."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.latitude, hass.config.longitude = 52.09, 5.12
    freezer.move_to(dt_util.as_utc(NOW))
    hass.states.async_set(GAS, "100", {"unit_of_measurement": "m³"})
    _import_history(hass, dt_util.as_utc(datetime(2025, 1, 1, tzinfo=TZ)), 20)
    await async_wait_recording_done(hass)


@pytest.mark.usefixtures("setup_integration_env", "history")
async def test_open_meteo(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """History from the archive, the last days from the forecast model."""
    fake = FakeOpenMeteo(final_until=TODAY - timedelta(days=6), now=NOW)
    fake.register(aioclient_mock)
    entry = await _setup(
        hass,
        {
            CONF_TEMPERATURE_SOURCE: "open_meteo",
            CONF_LOCATION: {"latitude": 52.1, "longitude": 5.18},
            CONF_HISTORY_START: "2023-01-01",
        },
    )
    assert entry.state is ConfigEntryState.LOADED
    # Three years in yearly chunks.
    assert fake.archive_requests[0][0] == date(2023, 1, 1)
    assert len(fake.archive_requests) == 3

    coordinator = entry.runtime_data
    jan10 = coordinator.records[date(2025, 1, 10)]
    assert jan10.mean_temperature == final_temperature(date(2025, 1, 10))
    assert jan10.usage is not None
    assert coordinator.records[date(2025, 1, 18)].mean_temperature == RECENT_TEMPERATURE
    assert (
        float(_state(hass, "mean_outdoor_temperature_yesterday")) == RECENT_TEMPERATURE
    )
    assert float(_state(hass, "mean_outdoor_temperature_today")) == RECENT_TEMPERATURE
    state = hass.states.get("sensor.home_weighted_degree_days_this_year")
    assert (
        state.attributes["attribution"] == "Weather data by Open-Meteo.com (CC BY 4.0)"
    )

    # Within the hour nothing is downloaded again.
    calls = len(aioclient_mock.mock_calls)
    await coordinator.async_refresh()
    assert len(aioclient_mock.mock_calls) == calls

    # After a reload the cached history is used: only recent days are queried.
    fake.archive_requests.clear()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(fake.archive_requests) == 1
    assert fake.archive_requests[0][0] > date(2024, 12, 1)
    assert entry.runtime_data.records[
        date(2023, 6, 1)
    ].mean_temperature == final_temperature(date(2023, 6, 1))


@pytest.mark.usefixtures("setup_integration_env", "history")
async def test_knmi_validated_replaces_provisional(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Days without validated data use 10-minute data until it is published."""
    fake = FakeKnmi(validated_until=TODAY - timedelta(days=10), now=dt_util.as_utc(NOW))
    fake.register(aioclient_mock)
    entry = await _setup(
        hass,
        {
            CONF_TEMPERATURE_SOURCE: "knmi",
            CONF_API_KEY: "secret",
            CONF_KNMI_STATION: DE_BILT,
            CONF_HISTORY_START: "2025-01-01",
        },
    )
    assert entry.state is ConfigEntryState.LOADED
    assert all(
        call[3] == {"Authorization": "secret"} for call in aioclient_mock.mock_calls
    )
    records = entry.runtime_data.records
    # The value stamped 2 January 00:00 UTC belongs to 1 January.
    assert records[date(2025, 1, 1)].mean_temperature == final_temperature(
        date(2025, 1, 1)
    )
    assert records[date(2025, 1, 11)].mean_temperature == final_temperature(
        date(2025, 1, 11)
    )
    provisional_day = date(2025, 1, 14)
    assert records[provisional_day].mean_temperature == RECENT_TEMPERATURE
    assert float(_state(hass, "mean_outdoor_temperature_today")) == RECENT_TEMPERATURE
    usage = records[provisional_day].usage
    assert usage is not None

    # KNMI publishes validated data up to 16 January; an hour later it is used,
    # also for days older than the regular refresh window.
    fake.validated_until = date(2025, 1, 16)
    fake.now += timedelta(hours=1, minutes=1)
    freezer.tick(timedelta(hours=1, minutes=1))
    await entry.runtime_data.async_refresh()
    records = entry.runtime_data.records
    assert records[provisional_day].mean_temperature == final_temperature(
        provisional_day
    )
    assert records[provisional_day].usage == usage
    assert records[date(2025, 1, 18)].mean_temperature == RECENT_TEMPERATURE


@pytest.mark.usefixtures("setup_integration_env", "history")
async def test_knmi_refused_key_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused API key asks the user for a new one."""
    aioclient_mock.get(
        "https://api.dataplatform.knmi.nl/edr/v1/collections", status=401
    )
    entry = await _setup(
        hass,
        {
            CONF_TEMPERATURE_SOURCE: "knmi",
            CONF_API_KEY: "wrong",
            CONF_KNMI_STATION: DE_BILT,
        },
    )
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.usefixtures("setup_integration_env", "history")
async def test_unreachable_without_cache_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without cached data an unreachable source retries the setup later."""
    aioclient_mock.get("https://archive-api.open-meteo.com/v1/archive", status=503)
    entry = await _setup(
        hass,
        {
            CONF_TEMPERATURE_SOURCE: "open_meteo",
            CONF_LOCATION: {"latitude": 52.1, "longitude": 5.18},
            CONF_HISTORY_START: "2025-01-01",
        },
    )
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.usefixtures("setup_integration_env", "history")
async def test_unreachable_with_cache_keeps_values(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With data already downloaded, an outage keeps the known values."""
    fake = FakeOpenMeteo(final_until=TODAY - timedelta(days=6), now=NOW)
    fake.register(aioclient_mock)
    entry = await _setup(
        hass,
        {
            CONF_TEMPERATURE_SOURCE: "open_meteo",
            CONF_LOCATION: {"latitude": 52.1, "longitude": 5.18},
            CONF_HISTORY_START: "2025-01-01",
        },
    )
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://archive-api.open-meteo.com/v1/archive", status=503)
    freezer.tick(timedelta(hours=1, minutes=1))
    await entry.runtime_data.async_refresh()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.last_update_success
    assert len(aioclient_mock.mock_calls) == 1  # the failed archive request
    assert entry.runtime_data.records[date(2025, 1, 10)].mean_temperature == (
        final_temperature(date(2025, 1, 10))
    )


@pytest.mark.usefixtures("setup_integration_env", "history")
async def test_failure_halfway_keeps_changes_for_later(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """New validated values are applied once a later update fully succeeds."""
    now = dt_util.as_utc(NOW)
    fake = FakeKnmi(validated_until=TODAY - timedelta(days=10), now=now)
    fake.register(aioclient_mock)
    entry = await _setup(
        hass,
        {
            CONF_TEMPERATURE_SOURCE: "knmi",
            CONF_API_KEY: "secret",
            CONF_KNMI_STATION: DE_BILT,
            CONF_HISTORY_START: "2025-01-01",
        },
    )
    day = date(2025, 1, 14)
    assert entry.runtime_data.records[day].mean_temperature == RECENT_TEMPERATURE

    # Validated data arrives, but the 10-minute request fails this time.
    fake.validated_until = date(2025, 1, 16)
    original = fake._handle

    async def ten_minute_fails(method, url, data):
        if "10-minute" in url.path and "/locations/" in url.path:
            return AiohttpClientMockResponse(method, url, status=500, text="error")
        return await original(method, url, data)

    fake._handle = ten_minute_fails
    aioclient_mock.clear_requests()
    fake.register(aioclient_mock)
    freezer.tick(timedelta(hours=1, minutes=1))
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.records[day].mean_temperature == RECENT_TEMPERATURE

    # The next update succeeds and applies the validated value.
    fake._handle = original
    aioclient_mock.clear_requests()
    fake.register(aioclient_mock)
    freezer.tick(timedelta(minutes=16))
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.records[day].mean_temperature == final_temperature(day)
