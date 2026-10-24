"""Data coordinator for the Degree Days integration.

All data is read from the Home Assistant recorder's long-term statistics,
so the full history of the selected sensors is available, not only the
period since this integration was installed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_metadata,
    statistic_during_period,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .calculations import (
    DayRecord,
    DegreeDayConfig,
    Periods,
    PeriodSummary,
    build_day_record,
    compare,
    preset_periods,
    same_day_previous_year,
    summarize,
    validate_periods,
    year_start,
)
from .const import (
    CONF_BASE_TEMPERATURE,
    CONF_BASELINE_USAGE,
    CONF_CONSUMPTION_SENSOR,
    CONF_HEATING_LIMIT,
    CONF_HISTORY_START,
    CONF_IMPORT_STATISTICS,
    CONF_TEMPERATURE_SENSOR,
    CONF_WEIGHTING,
    CONF_YEAR_START_MONTH,
    DEFAULT_BASE_TEMPERATURE,
    DEFAULT_BASELINE_USAGE,
    DEFAULT_HEATING_LIMIT,
    DEFAULT_HISTORY_YEARS,
    DEFAULT_IMPORT_STATISTICS,
    DEFAULT_PRESET,
    DEFAULT_WEIGHTING,
    DEFAULT_YEAR_START_MONTH,
    DOMAIN,
    PERIOD_KEYS,
    PRESET_CUSTOM,
    PRESET_OPTIONS,
    REFRESH_DAYS,
    STORAGE_VERSION,
    UNIT_DEGREE_DAYS,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type DegreeDaysConfigEntry = ConfigEntry[DegreeDaysCoordinator]

TEMPERATURE_UNITS = {"temperature": UnitOfTemperature.CELSIUS}


@dataclass(slots=True)
class DegreeDaysData:
    """Values exposed to the sensors."""

    today: DayRecord
    yesterday: DayRecord
    yesterday_summary: PeriodSummary
    month: PeriodSummary
    year: PeriodSummary
    last_30_days: PeriodSummary
    previous_year: PeriodSummary
    year_to_yesterday: PeriodSummary
    year_last_year_to_date: PeriodSummary
    year_comparison: dict[str, Any]
    period_comparison: PeriodComparison


@dataclass(slots=True)
class PeriodComparison:
    """Result of the user-selected period comparison."""

    periods: Periods
    error: str | None = None
    period_a: PeriodSummary | None = None
    period_b: PeriodSummary | None = None
    result: dict[str, Any] | None = None


def comparison_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding the comparison settings of an entry."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=dt_util.get_default_time_zone())


class DegreeDaysCoordinator(DataUpdateCoordinator[DegreeDaysData]):
    """Read statistics from the recorder and calculate degree days."""

    config_entry: DegreeDaysConfigEntry

    def __init__(self, hass: HomeAssistant, entry: DegreeDaysConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        options = {**entry.data, **entry.options}
        self.temperature_sensor: str = options[CONF_TEMPERATURE_SENSOR]
        self.consumption_sensor: str | None = options.get(CONF_CONSUMPTION_SENSOR)
        self.config = DegreeDayConfig(
            base_temperature=float(
                options.get(CONF_BASE_TEMPERATURE, DEFAULT_BASE_TEMPERATURE)
            ),
            heating_limit=float(options.get(CONF_HEATING_LIMIT, DEFAULT_HEATING_LIMIT)),
            weighting=options.get(CONF_WEIGHTING, DEFAULT_WEIGHTING),
            baseline_usage=float(
                options.get(CONF_BASELINE_USAGE, DEFAULT_BASELINE_USAGE)
            ),
        )
        self.year_start_month = int(
            options.get(CONF_YEAR_START_MONTH, DEFAULT_YEAR_START_MONTH)
        )
        self.import_statistics: bool = options.get(
            CONF_IMPORT_STATISTICS, DEFAULT_IMPORT_STATISTICS
        )
        history_start = options.get(CONF_HISTORY_START)
        self.history_start: date | None = (
            date.fromisoformat(history_start) if history_start else None
        )
        self.records: dict[date, DayRecord] = {}
        self._statistics_unit: str | None = None
        self._store = comparison_store(hass, entry.entry_id)
        self.preset: str = DEFAULT_PRESET
        self._custom_periods: Periods | None = None
        self._history_loaded = False
        # Values last written per statistic and day, to skip unchanged writes.
        self._written: dict[str, dict[date, tuple[float, float]]] = {}
        self._lock = asyncio.Lock()

    @property
    def usage_unit(self) -> str | None:
        """Return the unit of the consumption sensor."""
        if self.consumption_sensor is None:
            return None
        state = self.hass.states.get(self.consumption_sensor)
        if state is not None and (
            unit := state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        ):
            return unit
        # The meter may not be available yet (e.g. during startup): use the
        # unit of its statistics, which the usage values are expressed in.
        return self._statistics_unit

    def _default_history_start(self, today: date) -> date:
        if self.history_start is not None:
            return self.history_start
        return date(today.year - DEFAULT_HISTORY_YEARS, 1, 1)

    # Period comparison ---------------------------------------------------

    async def async_load_comparison(self) -> None:
        """Load the comparison settings saved by the date and select entities."""
        if not (stored := await self._store.async_load()):
            return
        if stored.get("preset") in PRESET_OPTIONS:
            self.preset = stored["preset"]
        try:
            self._custom_periods = Periods.from_dict(
                {key: date.fromisoformat(stored[key]) for key in PERIOD_KEYS}
            )
        except (KeyError, TypeError, ValueError):
            self._custom_periods = None

    def current_periods(self, today: date | None = None) -> Periods:
        """Return the periods currently being compared."""
        today = today or dt_util.now().date()
        periods = preset_periods(self.preset, today, self.year_start_month)
        if periods is not None:
            return periods
        if self._custom_periods is not None:
            return self._custom_periods
        # Custom without saved dates: start from the default preset.
        periods = preset_periods(DEFAULT_PRESET, today, self.year_start_month)
        assert periods is not None
        return periods

    async def async_set_preset(self, preset: str) -> None:
        """Select a preset.

        Switching to "custom" restores the last custom dates, or starts from
        the dates currently shown when there are none yet.
        """
        if preset == PRESET_CUSTOM and self._custom_periods is None:
            self._custom_periods = self.current_periods()
        self.preset = preset
        await self._async_comparison_changed()

    async def async_set_period_date(self, key: str, value: date) -> None:
        """Change one date; this switches the comparison to custom."""
        values = self.current_periods().as_dict()
        values[key] = value
        self._custom_periods = Periods.from_dict(values)
        self.preset = PRESET_CUSTOM
        await self._async_comparison_changed()

    async def _async_comparison_changed(self) -> None:
        data: dict[str, Any] = {"preset": self.preset}
        if self._custom_periods is not None:
            data.update(
                {
                    key: value.isoformat()
                    for key, value in self._custom_periods.as_dict().items()
                }
            )
        await self._store.async_save(data)
        # Recalculate from the cached days only; no database access.
        self.async_set_updated_data(self._build_data(dt_util.now().date()))

    def _compare_periods(self, today: date) -> PeriodComparison:
        periods = self.current_periods(today)
        if error := validate_periods(periods):
            return PeriodComparison(periods=periods, error=error)
        period_a = self.summarize(periods.a_start, periods.a_end)
        period_b = self.summarize(periods.b_start, periods.b_end)
        if not period_a.days_with_temperature or not period_b.days_with_temperature:
            error = "no_data"
        return PeriodComparison(
            periods=periods,
            error=error,
            period_a=period_a,
            period_b=period_b,
            result=compare(period_a, period_b),
        )

    async def async_rebuild_history(self) -> None:
        """Discard the cache and read the complete history again."""
        async with self._lock:
            self.records = {}
            self._history_loaded = False
        await self.async_refresh()

    async def _async_update_data(self) -> DegreeDaysData:
        async with self._lock:
            today = dt_util.now().date()
            if not self._history_loaded:
                start = self._default_history_start(today)
            else:
                start = today - timedelta(days=REFRESH_DAYS)
            full_load = not self._history_loaded
            if full_load and self.consumption_sensor:
                await self._async_load_statistics_unit()
            await self._async_load_days(start, today)
            await self._async_load_today(today)
            self._history_loaded = True
            if self.import_statistics:
                self._import_statistics(today, full_load)
            elif full_load:
                # Remove statistics written while the option was enabled.
                get_instance(self.hass).async_clear_statistics(self.statistic_ids)
            return self._build_data(today)

    async def _async_load_statistics_unit(self) -> None:
        """Read the unit of the consumption meter's statistics."""
        assert self.consumption_sensor is not None
        metadata = await get_instance(self.hass).async_add_executor_job(
            partial(get_metadata, self.hass, statistic_ids={self.consumption_sensor})
        )
        if meta := metadata.get(self.consumption_sensor):
            self._statistics_unit = meta[1]["unit_of_measurement"]

    async def _async_load_days(self, start: date, today: date) -> None:
        """Read daily statistics for start..today into the cache."""
        recorder = get_instance(self.hass)
        start_time = dt_util.as_utc(_local_midnight(start))
        end_time = dt_util.as_utc(_local_midnight(today + timedelta(days=1)))

        temperatures = await recorder.async_add_executor_job(
            statistics_during_period,
            self.hass,
            start_time,
            end_time,
            {self.temperature_sensor},
            "day",
            TEMPERATURE_UNITS,
            {"mean"},
        )
        usages: dict[str, list[Any]] = {}
        if self.consumption_sensor:
            usages = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start_time,
                end_time,
                {self.consumption_sensor},
                "day",
                None,
                {"change"},
            )

        day_temps = self._rows_by_day(
            temperatures.get(self.temperature_sensor, []), "mean"
        )
        day_usage = (
            self._rows_by_day(usages.get(self.consumption_sensor, []), "change")
            if self.consumption_sensor
            else {}
        )

        current = start
        while current <= today:
            temp = day_temps.get(current)
            usage = day_usage.get(current)
            if temp is not None or usage is not None:
                self.records[current] = build_day_record(
                    current, temp, usage, self.config
                )
            current += timedelta(days=1)

    @staticmethod
    def _rows_by_day(rows: list[Any], key: str) -> dict[date, float]:
        result: dict[date, float] = {}
        for row in rows:
            value = row.get(key)
            if value is None:
                continue
            start = dt_util.as_local(dt_util.utc_from_timestamp(row["start"]))
            result[start.date()] = float(value)
        return result

    async def _async_load_today(self, today: date) -> None:
        """Read today's values including the 5 minute statistics."""
        recorder = get_instance(self.hass)
        start_time = dt_util.as_utc(_local_midnight(today))

        temp_result = await recorder.async_add_executor_job(
            statistic_during_period,
            self.hass,
            start_time,
            None,
            self.temperature_sensor,
            {"mean"},
            TEMPERATURE_UNITS,
        )
        temperature = temp_result.get("mean")
        if temperature is None and (
            state := self.hass.states.get(self.temperature_sensor)
        ):
            # No statistics yet for today: use the current state.
            try:
                temperature = float(state.state)
            except ValueError:
                temperature = None
            else:
                unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                if unit == UnitOfTemperature.FAHRENHEIT:
                    temperature = (temperature - 32) * 5 / 9

        usage = None
        if self.consumption_sensor:
            usage_result = await recorder.async_add_executor_job(
                statistic_during_period,
                self.hass,
                start_time,
                None,
                self.consumption_sensor,
                {"change"},
                None,
            )
            usage = usage_result.get("change")

        if temperature is None and usage is None:
            self.records.pop(today, None)
            return
        self.records[today] = build_day_record(
            today,
            float(temperature) if temperature is not None else None,
            float(usage) if usage is not None else None,
            self.config,
        )

    def _build_data(self, today: date) -> DegreeDaysData:
        yesterday = today - timedelta(days=1)
        ys = year_start(today, self.year_start_month)
        previous_ys = year_start(ys - timedelta(days=1), self.year_start_month)
        year = summarize(self.records, ys, today, self.config)
        # The year-on-year comparison uses completed days only, so a partial
        # today is not compared with a full day last year.
        year_to_yesterday = summarize(self.records, ys, yesterday, self.config)
        last_year_to_date = summarize(
            self.records,
            previous_ys,
            same_day_previous_year(yesterday),
            self.config,
        )
        return DegreeDaysData(
            today=self.records.get(today) or DayRecord(day=today),
            yesterday=self.records.get(yesterday) or DayRecord(day=yesterday),
            yesterday_summary=summarize(
                self.records, yesterday, yesterday, self.config
            ),
            month=summarize(self.records, today.replace(day=1), today, self.config),
            year=year,
            last_30_days=summarize(
                self.records, today - timedelta(days=29), today, self.config
            ),
            previous_year=summarize(
                self.records, previous_ys, ys - timedelta(days=1), self.config
            ),
            year_last_year_to_date=last_year_to_date,
            year_to_yesterday=year_to_yesterday,
            year_comparison=compare(last_year_to_date, year_to_yesterday),
            period_comparison=self._compare_periods(today),
        )

    def summarize(self, start: date, end: date) -> PeriodSummary:
        """Summarise an arbitrary period from the cache."""
        return summarize(self.records, start, end, self.config)

    # External statistics -------------------------------------------------

    def statistic_id(self, key: str) -> str:
        """Return the external statistic id for a key."""
        return f"{DOMAIN}:{self.config_entry.entry_id.lower()}_{key}"

    def _import_statistics(self, today: date, full_load: bool) -> None:
        """Write completed days as external statistics.

        This makes (weighted) degree days available in the statistics graph
        card for the full history, at day/week/month/year resolution. The
        cumulative sum is recalculated from the first known day each time.
        On a full load the old series is cleared first, so a changed history
        start or setting cannot leave an inconsistent sum behind. Afterwards
        only days whose value changed are written.
        """
        completed = sorted(day for day in self.records if day < today)
        if full_load:
            self._written = {}
            get_instance(self.hass).async_clear_statistics(self.statistic_ids)
        if not completed:
            return

        for key, name in (
            ("degree_days", "Degree days"),
            ("weighted_degree_days", "Weighted degree days"),
        ):
            statistic_id = self.statistic_id(key)
            written = self._written.setdefault(statistic_id, {})
            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"{self.config_entry.title} {name.lower()}",
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class=None,
                unit_of_measurement=UNIT_DEGREE_DAYS,
            )
            statistics: list[StatisticData] = []
            total = 0.0
            for day in completed:
                value = round(getattr(self.records[day], key) or 0.0, 4)
                total = round(total + value, 4)
                if written.get(day) == (value, total):
                    continue
                written[day] = (value, total)
                statistics.append(
                    StatisticData(start=_local_midnight(day), state=value, sum=total)
                )
            if statistics:
                async_add_external_statistics(self.hass, metadata, statistics)

    @property
    def statistic_ids(self) -> list[str]:
        """Return the ids of the external statistics of this entry."""
        return [
            self.statistic_id("degree_days"),
            self.statistic_id("weighted_degree_days"),
        ]
