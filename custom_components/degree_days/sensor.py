"""Sensors for the Degree Days integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .calculations import PeriodSummary
from .const import UNIT_DEGREE_DAYS
from .coordinator import (
    DegreeDaysConfigEntry,
    DegreeDaysCoordinator,
    DegreeDaysData,
    PeriodComparison,
)
from .entity import DegreeDaysEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DegreeDaysSensorDescription(SensorEntityDescription):
    """Describes a Degree Days sensor."""

    value_fn: Callable[[DegreeDaysData], float | None]
    attributes_fn: Callable[[DegreeDaysData], dict[str, Any]] | None = None
    # Unit is the unit of the consumption sensor (optionally "per degree day").
    usage_unit: bool = False
    per_degree_day: bool = False
    requires_consumption: bool = False
    digits: int = 2


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def _summary_attrs(
    fn: Callable[[DegreeDaysData], PeriodSummary],
) -> Callable[[DegreeDaysData], dict[str, Any]]:
    return lambda data: fn(data).as_dict()


def _comparison(key: str) -> Callable[[DegreeDaysData], float | None]:
    def value(data: DegreeDaysData) -> float | None:
        comparison = data.period_comparison
        if comparison.error or comparison.result is None:
            return None
        return comparison.result[key]

    return value


def _comparison_period(
    period: str, key: str
) -> Callable[[DegreeDaysData], float | None]:
    def value(data: DegreeDaysData) -> float | None:
        comparison = data.period_comparison
        summary = comparison.period_a if period == "a" else comparison.period_b
        if comparison.error or summary is None:
            return None
        return getattr(summary, key)

    return value


def _comparison_attrs(data: DegreeDaysData) -> dict[str, Any]:
    comparison: PeriodComparison = data.period_comparison
    periods = comparison.periods
    return {
        "period_a": f"{periods.a_start.isoformat()} – {periods.a_end.isoformat()}",
        "period_b": f"{periods.b_start.isoformat()} – {periods.b_end.isoformat()}",
        "problem": comparison.error,
        "period_a_summary": (
            comparison.period_a.as_dict() if comparison.period_a else None
        ),
        "period_b_summary": (
            comparison.period_b.as_dict() if comparison.period_b else None
        ),
        "comparison": comparison.result,
    }


TEMPERATURE_SENSORS: tuple[DegreeDaysSensorDescription, ...] = (
    DegreeDaysSensorDescription(
        key="mean_temperature_today",
        translation_key="mean_temperature_today",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.today.mean_temperature,
        digits=1,
    ),
    DegreeDaysSensorDescription(
        key="mean_temperature_yesterday",
        translation_key="mean_temperature_yesterday",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.yesterday.mean_temperature,
        digits=1,
    ),
    DegreeDaysSensorDescription(
        key="degree_days_today",
        translation_key="degree_days_today",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.today.degree_days,
    ),
    DegreeDaysSensorDescription(
        key="degree_days_yesterday",
        translation_key="degree_days_yesterday",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.yesterday.degree_days,
    ),
    DegreeDaysSensorDescription(
        key="weighted_degree_days_today",
        translation_key="weighted_degree_days_today",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.today.weighted_degree_days,
    ),
    DegreeDaysSensorDescription(
        key="weighted_degree_days_yesterday",
        translation_key="weighted_degree_days_yesterday",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.yesterday.weighted_degree_days,
    ),
    DegreeDaysSensorDescription(
        key="weighted_degree_days_month",
        translation_key="weighted_degree_days_month",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.month.weighted_degree_days,
        attributes_fn=_summary_attrs(lambda d: d.month),
    ),
    DegreeDaysSensorDescription(
        key="weighted_degree_days_year",
        translation_key="weighted_degree_days_year",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.year.weighted_degree_days,
        attributes_fn=_summary_attrs(lambda d: d.year),
    ),
    DegreeDaysSensorDescription(
        key="weighted_degree_days_last_year_to_date",
        translation_key="weighted_degree_days_last_year_to_date",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.year_last_year_to_date.weighted_degree_days,
        attributes_fn=_summary_attrs(lambda d: d.year_last_year_to_date),
    ),
    DegreeDaysSensorDescription(
        key="weighted_degree_days_previous_year",
        translation_key="weighted_degree_days_previous_year",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=lambda d: d.previous_year.weighted_degree_days,
        attributes_fn=_summary_attrs(lambda d: d.previous_year),
    ),
    DegreeDaysSensorDescription(
        key="comparison_weighted_degree_days_a",
        translation_key="comparison_weighted_degree_days_a",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=_comparison_period("a", "weighted_degree_days"),
    ),
    DegreeDaysSensorDescription(
        key="comparison_weighted_degree_days_b",
        translation_key="comparison_weighted_degree_days_b",
        native_unit_of_measurement=UNIT_DEGREE_DAYS,
        value_fn=_comparison_period("b", "weighted_degree_days"),
    ),
)

CONSUMPTION_SENSORS: tuple[DegreeDaysSensorDescription, ...] = (
    DegreeDaysSensorDescription(
        key="usage_today",
        translation_key="usage_today",
        usage_unit=True,
        value_fn=lambda d: d.today.usage,
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="usage_yesterday",
        translation_key="usage_yesterday",
        usage_unit=True,
        value_fn=lambda d: d.yesterday.usage,
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="usage_month",
        translation_key="usage_month",
        usage_unit=True,
        value_fn=lambda d: d.month.usage,
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="usage_year",
        translation_key="usage_year",
        usage_unit=True,
        value_fn=lambda d: d.year.usage,
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="usage_last_year_to_date",
        translation_key="usage_last_year_to_date",
        usage_unit=True,
        value_fn=lambda d: d.year_last_year_to_date.usage,
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="usage_per_weighted_degree_day_yesterday",
        translation_key="usage_per_weighted_degree_day_yesterday",
        usage_unit=True,
        per_degree_day=True,
        value_fn=lambda d: d.yesterday_summary.usage_per_weighted_degree_day,
        digits=4,
    ),
    DegreeDaysSensorDescription(
        key="usage_per_weighted_degree_day_30d",
        translation_key="usage_per_weighted_degree_day_30d",
        usage_unit=True,
        per_degree_day=True,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.last_30_days.usage_per_weighted_degree_day,
        attributes_fn=_summary_attrs(lambda d: d.last_30_days),
        digits=4,
    ),
    DegreeDaysSensorDescription(
        key="usage_per_weighted_degree_day_year",
        translation_key="usage_per_weighted_degree_day_year",
        usage_unit=True,
        per_degree_day=True,
        value_fn=lambda d: d.year.usage_per_weighted_degree_day,
        attributes_fn=_summary_attrs(lambda d: d.year),
        digits=4,
    ),
    DegreeDaysSensorDescription(
        key="usage_per_weighted_degree_day_previous_year",
        translation_key="usage_per_weighted_degree_day_previous_year",
        usage_unit=True,
        per_degree_day=True,
        value_fn=lambda d: d.previous_year.usage_per_weighted_degree_day,
        attributes_fn=_summary_attrs(lambda d: d.previous_year),
        digits=4,
    ),
    DegreeDaysSensorDescription(
        key="weather_corrected_usage_year",
        translation_key="weather_corrected_usage_year",
        usage_unit=True,
        value_fn=lambda d: d.year_comparison["weather_corrected_usage"],
        attributes_fn=lambda d: {
            "this_year": d.year_to_yesterday.as_dict(),
            "last_year_to_date": d.year_last_year_to_date.as_dict(),
            **d.year_comparison,
        },
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="weather_corrected_difference_year",
        translation_key="weather_corrected_difference_year",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.year_comparison["weather_corrected_difference_pct"],
        attributes_fn=lambda d: dict(d.year_comparison),
        digits=1,
    ),
    DegreeDaysSensorDescription(
        key="comparison_weather_corrected_difference",
        translation_key="comparison_weather_corrected_difference",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_comparison("weather_corrected_difference_pct"),
        attributes_fn=_comparison_attrs,
        digits=1,
    ),
    DegreeDaysSensorDescription(
        key="comparison_usage_per_weighted_degree_day_difference",
        translation_key="comparison_usage_per_weighted_degree_day_difference",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_comparison("usage_per_weighted_degree_day_difference_pct"),
        digits=1,
    ),
    DegreeDaysSensorDescription(
        key="comparison_usage_difference",
        translation_key="comparison_usage_difference",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_comparison("usage_difference_pct"),
        digits=1,
    ),
    DegreeDaysSensorDescription(
        key="comparison_usage_a",
        translation_key="comparison_usage_a",
        usage_unit=True,
        value_fn=_comparison_period("a", "usage"),
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="comparison_usage_b",
        translation_key="comparison_usage_b",
        usage_unit=True,
        value_fn=_comparison_period("b", "usage"),
        digits=3,
    ),
    DegreeDaysSensorDescription(
        key="comparison_usage_per_weighted_degree_day_a",
        translation_key="comparison_usage_per_weighted_degree_day_a",
        usage_unit=True,
        per_degree_day=True,
        value_fn=_comparison_period("a", "usage_per_weighted_degree_day"),
        digits=4,
    ),
    DegreeDaysSensorDescription(
        key="comparison_usage_per_weighted_degree_day_b",
        translation_key="comparison_usage_per_weighted_degree_day_b",
        usage_unit=True,
        per_degree_day=True,
        value_fn=_comparison_period("b", "usage_per_weighted_degree_day"),
        digits=4,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DegreeDaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    descriptions = list(TEMPERATURE_SENSORS)
    if coordinator.consumption_sensor:
        descriptions.extend(CONSUMPTION_SENSORS)
    async_add_entities(
        DegreeDaysSensor(coordinator, description) for description in descriptions
    )


class DegreeDaysSensor(DegreeDaysEntity, SensorEntity):
    """A degree days sensor."""

    # The comparison details change with every update; keep them out of the
    # recorder database (they remain visible as attributes).
    _unrecorded_attributes = frozenset(
        {"period_a_summary", "period_b_summary", "comparison"}
    )
    entity_description: DegreeDaysSensorDescription

    def __init__(
        self,
        coordinator: DegreeDaysCoordinator,
        description: DegreeDaysSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        description = self.entity_description
        if not description.usage_unit:
            return description.native_unit_of_measurement
        unit = self.coordinator.usage_unit
        if unit and description.per_degree_day:
            return f"{unit}/{UNIT_DEGREE_DAYS}"
        return unit

    @property
    def native_value(self) -> float | None:
        """Return the state."""
        description = self.entity_description
        return _round(description.value_fn(self.coordinator.data), description.digits)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
