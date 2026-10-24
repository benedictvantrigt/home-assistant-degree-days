"""Remote outdoor temperature sources: Open-Meteo and KNMI.

Each source returns daily mean temperatures in two flavours:

* final values, which no longer change (Open-Meteo ERA5-Land reanalysis,
  KNMI validated station data), and
* recent values, which cover the last days that have no final value yet,
  including today so far (Open-Meteo forecast model, KNMI 10-minute data).

``RemoteTemperatures`` combines both, keeps the final values in a cache file
so the history is only downloaded once, and limits how often the source is
queried.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
import logging
import math
from typing import Any

import aiohttp

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_KNMI_STATION,
    CONF_LOCATION,
    CONF_TEMPERATURE_SOURCE,
    DOMAIN,
    PROVISIONAL_DAYS,
    REMOTE_UPDATE_INTERVAL,
    SOURCE_KNMI,
    SOURCE_OPEN_METEO,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
RETRY_AFTER_ERROR = timedelta(minutes=15)
CACHE_VERSION = 1


class SourceError(Exception):
    """The source could not be reached or returned an unusable answer."""


class SourceAuthError(SourceError):
    """The source rejected the credentials."""


def _chunks(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Split start..end (inclusive) into ranges of at most ``days`` days."""
    current = start
    while current <= end:
        chunk_end = min(end, current + timedelta(days=days - 1))
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def _mean_per_day(values: list[tuple[date, float]]) -> dict[date, float]:
    grouped: dict[date, list[float]] = defaultdict(list)
    for day, value in values:
        grouped[day].append(value)
    return {day: sum(items) / len(items) for day, items in grouped.items()}


class TemperatureSource(ABC):
    """A provider of daily mean outdoor temperatures."""

    attribution: str

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the source."""
        self._session = session

    @property
    @abstractmethod
    def cache_key(self) -> str:
        """Identify the data, so a cache of another location is not reused."""

    @abstractmethod
    async def async_fetch_final(self, start: date, end: date) -> dict[date, float]:
        """Return final daily means for start..end; missing days are left out."""

    @abstractmethod
    async def async_fetch_recent(self, start: date, now: datetime) -> dict[date, float]:
        """Return provisional daily means from start until now (today so far)."""

    async def _get_json(
        self,
        url: str,
        params: Mapping[str, str | int | float] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        try:
            async with self._session.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status in (401, 403):
                    raise SourceAuthError(f"{url} refused the credentials")
                if response.status >= 400:
                    text = (await response.text())[:200]
                    raise SourceError(f"{url} returned {response.status}: {text}")
                return await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise SourceError(f"{url} failed: {err}") from err


class OpenMeteoSource(TemperatureSource):
    """Open-Meteo: ERA5-Land reanalysis for history, forecast model for recent days.

    Days are local calendar days in Home Assistant's time zone.
    """

    ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
    attribution = "Weather data by Open-Meteo.com (CC BY 4.0)"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        latitude: float,
        longitude: float,
        time_zone: tzinfo,
    ) -> None:
        """Initialise the source."""
        super().__init__(session)
        self.latitude = round(latitude, 4)
        self.longitude = round(longitude, 4)
        self.time_zone = time_zone

    @property
    def cache_key(self) -> str:
        """Identify the data."""
        return f"open_meteo:{self.latitude:.4f},{self.longitude:.4f}:{self.time_zone}"

    def _params(self) -> dict[str, str | int | float]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timezone": str(self.time_zone),
        }

    async def async_fetch_final(self, start: date, end: date) -> dict[date, float]:
        """Return ERA5-Land daily means (available with a delay of ~5 days)."""
        result: dict[date, float] = {}
        for chunk_start, chunk_end in _chunks(start, end, 366):
            data = await self._get_json(
                self.ARCHIVE_URL,
                {
                    **self._params(),
                    "start_date": chunk_start.isoformat(),
                    "end_date": chunk_end.isoformat(),
                    "daily": "temperature_2m_mean",
                    "models": "era5_land",
                },
            )
            daily = data.get("daily") or {}
            for day, value in zip(
                daily.get("time", []),
                daily.get("temperature_2m_mean", []),
                strict=False,
            ):
                if value is not None:
                    result[date.fromisoformat(day)] = float(value)
        return result

    async def async_fetch_recent(self, start: date, now: datetime) -> dict[date, float]:
        """Return daily means of the hourly forecast-model values up to now."""
        local_now = now.astimezone(self.time_zone).replace(tzinfo=None)
        past_days = max(1, (local_now.date() - start).days)
        data = await self._get_json(
            self.FORECAST_URL,
            {
                **self._params(),
                "hourly": "temperature_2m",
                "past_days": past_days,
                "forecast_days": 1,
            },
        )
        hourly = data.get("hourly") or {}
        values: list[tuple[date, float]] = []
        for moment, value in zip(
            hourly.get("time", []), hourly.get("temperature_2m", []), strict=False
        ):
            timestamp = datetime.fromisoformat(moment)
            if value is None or timestamp > local_now or timestamp.date() < start:
                continue
            values.append((timestamp.date(), float(value)))
        return _mean_per_day(values)


@dataclass(frozen=True, slots=True)
class KnmiStation:
    """A KNMI weather station."""

    station_id: str
    name: str
    latitude: float | None
    longitude: float | None

    def distance_km(self, latitude: float, longitude: float) -> float | None:
        """Return the distance to a location."""
        if self.latitude is None or self.longitude is None:
            return None
        lat1, lon1, lat2, lon2 = map(
            math.radians, (latitude, longitude, self.latitude, self.longitude)
        )
        a = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
        return 6371 * 2 * math.asin(math.sqrt(a))


class KnmiSource(TemperatureSource):
    """KNMI Data Platform EDR API: validated daily TG plus 10-minute data.

    KNMI's daily values cover UTC days. Validated data can be published up to
    about ten working days late; the gap is filled with the mean of the
    10-minute observations, which is replaced once the validated value exists.
    """

    BASE_URL = "https://api.dataplatform.knmi.nl/edr/v1"
    DAILY_COLLECTION = "daily-in-situ-meteorological-observations-validated"
    TEN_MINUTE_COLLECTION = "10-minute-in-situ-meteorological-observations"
    DAILY_PARAMETER = "TG"
    TEN_MINUTE_PARAMETER = "ta"
    attribution = "Weather data by KNMI"

    def __init__(
        self, session: aiohttp.ClientSession, api_key: str, station_id: str | None
    ) -> None:
        """Initialise the source."""
        super().__init__(session)
        self._api_key = api_key
        self.station_id = station_id
        self._discovered = False
        self._daily_collection = self.DAILY_COLLECTION
        self._ten_minute_collection = self.TEN_MINUTE_COLLECTION
        self._daily_parameter = self.DAILY_PARAMETER
        self._ten_minute_parameter = self.TEN_MINUTE_PARAMETER

    @property
    def cache_key(self) -> str:
        """Identify the data."""
        return f"knmi:{self.station_id}"

    async def _get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        return await self._get_json(
            f"{self.BASE_URL}{path}", params, {"Authorization": self._api_key}
        )

    async def _async_discover(self) -> None:
        """Look up the collection and parameter names.

        Falls back to the documented names when the listing has no match, so
        a change in the listing does not stop the integration by itself.
        """
        if self._discovered:
            return
        listing = await self._get("/collections")
        collections = {
            item.get("id"): item
            for item in listing.get("collections", [])
            if isinstance(item, dict)
        }
        if self.DAILY_COLLECTION not in collections:
            for collection_id in collections:
                name = str(collection_id)
                if "daily" in name and "in-situ" in name and "validated" in name:
                    self._daily_collection = name
                    break
        self._daily_parameter = _pick_parameter(
            collections.get(self._daily_collection), self.DAILY_PARAMETER
        )
        self._ten_minute_parameter = _pick_parameter(
            collections.get(self._ten_minute_collection), self.TEN_MINUTE_PARAMETER
        )
        self._discovered = True
        _LOGGER.debug(
            "KNMI collections %s (%s) and %s (%s)",
            self._daily_collection,
            self._daily_parameter,
            self._ten_minute_collection,
            self._ten_minute_parameter,
        )

    async def async_stations(self) -> list[KnmiStation]:
        """Return the stations with validated daily data; validates the key."""
        await self._async_discover()
        data = await self._get(f"/collections/{self._daily_collection}/locations")
        stations: list[KnmiStation] = []
        for feature in data.get("features", []):
            station_id = feature.get("id")
            if not station_id:
                continue
            properties = feature.get("properties") or {}
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            stations.append(
                KnmiStation(
                    station_id=str(station_id),
                    name=str(properties.get("name") or station_id),
                    latitude=float(coordinates[1]) if len(coordinates) > 1 else None,
                    longitude=float(coordinates[0]) if coordinates else None,
                )
            )
        return stations

    async def _async_series(
        self, collection: str, parameter: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, float]]:
        data = await self._get(
            f"/collections/{collection}/locations/{self.station_id}",
            {
                "datetime": f"{_iso(start)}/{_iso(end)}",
                "parameter-name": parameter,
            },
        )
        return _coverage_series(data, parameter)

    async def async_fetch_final(self, start: date, end: date) -> dict[date, float]:
        """Return validated daily mean temperatures (TG)."""
        await self._async_discover()
        result: dict[date, float] = {}
        for chunk_start, chunk_end in _chunks(start, end, 366):
            series = await self._async_series(
                self._daily_collection,
                self._daily_parameter,
                _utc_midnight(chunk_start),
                _utc_midnight(chunk_end + timedelta(days=1)),
            )
            for moment, value in series:
                # The time marks the end of the measuring interval.
                day = (moment - timedelta(seconds=1)).date()
                if chunk_start <= day <= chunk_end:
                    result[day] = value
        return result

    async def async_fetch_recent(self, start: date, now: datetime) -> dict[date, float]:
        """Return daily means of the 10-minute air temperature up to now."""
        await self._async_discover()
        values: list[tuple[date, float]] = []
        end = now.astimezone(UTC)
        for chunk_start, chunk_end in _chunks(start, end.date(), 2):
            series = await self._async_series(
                self._ten_minute_collection,
                self._ten_minute_parameter,
                _utc_midnight(chunk_start),
                min(end, _utc_midnight(chunk_end + timedelta(days=1))),
            )
            for moment, value in series:
                day = (moment - timedelta(seconds=1)).date()
                if day >= start:
                    values.append((day, value))
        return _mean_per_day(values)


def _pick_parameter(collection: Mapping[str, Any] | None, default: str) -> str:
    if not collection:
        return default
    names = list((collection.get("parameter_names") or {}).keys())
    for name in names:
        if name == default:
            return name
    for name in names:
        if name.lower() == default.lower():
            return name
    return default


def _utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coverage_series(payload: Any, parameter: str) -> list[tuple[datetime, float]]:
    """Read (time, value) pairs from a CoverageJSON coverage (collection)."""
    if not isinstance(payload, dict):
        raise SourceError("Unexpected KNMI response")
    coverages = (
        payload.get("coverages", [])
        if payload.get("type") == "CoverageCollection"
        else [payload]
    )
    result: list[tuple[datetime, float]] = []
    for coverage in coverages:
        try:
            times = coverage["domain"]["axes"]["t"]["values"]
            values = coverage["ranges"][parameter]["values"]
        except (KeyError, TypeError) as err:
            raise SourceError(f"KNMI response has no {parameter} series") from err
        for moment, value in zip(times, values, strict=False):
            if value is None:
                continue
            timestamp = dt_util.parse_datetime(moment)
            if timestamp is None:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            result.append((timestamp, float(value)))
    return result


def create_source(hass: HomeAssistant, options: Mapping[str, Any]) -> TemperatureSource:
    """Create the remote source configured in an entry."""
    session = async_get_clientsession(hass)
    source = options[CONF_TEMPERATURE_SOURCE]
    if source == SOURCE_OPEN_METEO:
        location = options.get(CONF_LOCATION) or {}
        return OpenMeteoSource(
            session,
            float(location.get("latitude", hass.config.latitude)),
            float(location.get("longitude", hass.config.longitude)),
            dt_util.get_default_time_zone(),
        )
    if source == SOURCE_KNMI:
        return KnmiSource(session, options[CONF_API_KEY], options[CONF_KNMI_STATION])
    raise ValueError(f"Not a remote source: {source}")


def temperatures_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the cache file of the remote temperatures of an entry."""
    return Store(hass, CACHE_VERSION, f"{DOMAIN}.{entry_id}.temperatures")


class RemoteTemperatures:
    """Combine final and recent values of a source and cache the final ones."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, source: TemperatureSource
    ) -> None:
        """Initialise."""
        self.source = source
        self._store = temperatures_store(hass, entry_id)
        self.final: dict[date, float] = {}
        self.recent: dict[date, float] = {}
        self._covered_from: date | None = None
        self._next_update: datetime | None = None
        self._failing = False
        self._lock = asyncio.Lock()

    def temperature(self, day: date) -> float | None:
        """Return the best known daily mean of a day."""
        if (value := self.final.get(day)) is not None:
            return value
        return self.recent.get(day)

    async def async_load(self) -> None:
        """Load cached final values of the same source."""
        stored = await self._store.async_load()
        if not stored or stored.get("source") != self.source.cache_key:
            return
        self.final = {
            date.fromisoformat(day): float(value)
            for day, value in (stored.get("final") or {}).items()
        }
        if covered_from := stored.get("covered_from"):
            self._covered_from = date.fromisoformat(covered_from)

    async def async_clear(self) -> None:
        """Forget all values, so the next update downloads everything again."""
        async with self._lock:
            self.final = {}
            self.recent = {}
            self._covered_from = None
            self._next_update = None
            await self._store.async_remove()

    async def async_update(
        self, history_start: date, today: date, now: datetime
    ) -> set[date]:
        """Query the source when due; return the days whose value changed.

        Raises SourceAuthError when the credentials are refused, and
        SourceError when the source failed and nothing is cached yet.
        """
        async with self._lock:
            if self._next_update is not None and now < self._next_update:
                return set()
            before = {
                day: self.temperature(day) for day in set(self.final) | set(self.recent)
            }
            try:
                await self._async_fetch(history_start, today, now)
            except SourceAuthError:
                raise
            except SourceError as err:
                self._next_update = now + RETRY_AFTER_ERROR
                if not self.final and not self.recent:
                    raise
                if not self._failing:
                    _LOGGER.warning(
                        "Could not update temperatures (%s), using cached values: %s",
                        self.source.attribution,
                        err,
                    )
                self._failing = True
                return set()
            if self._failing:
                _LOGGER.info(
                    "Temperatures are updated again (%s)", self.source.attribution
                )
            self._failing = False
            self._next_update = now + REMOTE_UPDATE_INTERVAL
            after_days = set(before) | set(self.final) | set(self.recent)
            return {
                day for day in after_days if before.get(day) != self.temperature(day)
            }

    async def _async_fetch(
        self, history_start: date, today: date, now: datetime
    ) -> None:
        yesterday = today - timedelta(days=1)
        ranges: list[tuple[date, date]] = []
        if self._covered_from is None or not self.final:
            ranges.append((history_start, yesterday))
        else:
            if history_start < self._covered_from:
                ranges.append((history_start, self._covered_from - timedelta(days=1)))
            # Recent days may have been finalised since the last query.
            latest = max(self.final)
            refresh_from = min(latest + timedelta(days=1), yesterday) - timedelta(
                days=PROVISIONAL_DAYS
            )
            ranges.append((max(history_start, refresh_from), yesterday))
        # Download into new dicts and apply them only when everything
        # succeeded, so a failure halfway leaves the previous state intact.
        final = dict(self.final)
        for start, end in ranges:
            if start <= end:
                final.update(await self.source.async_fetch_final(start, end))

        recent_start = today - timedelta(days=PROVISIONAL_DAYS)
        if final:
            latest_final = min(max(final), today)
            recent_start = max(recent_start, latest_final + timedelta(days=1))
        recent = (
            await self.source.async_fetch_recent(recent_start, now)
            if recent_start <= today
            else {}
        )

        covered_from = min(history_start, self._covered_from or history_start)
        unchanged = final == self.final and covered_from == self._covered_from
        self.final, self.recent = final, recent
        if unchanged:
            return
        self._covered_from = covered_from
        await self._store.async_save(
            {
                "source": self.source.cache_key,
                "covered_from": self._covered_from.isoformat(),
                "final": {
                    day.isoformat(): round(value, 2)
                    for day, value in sorted(self.final.items())
                },
            }
        )
