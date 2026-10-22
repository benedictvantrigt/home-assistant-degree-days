"""Pure degree day and consumption calculations.

This module has no Home Assistant dependencies so it can be unit tested in
isolation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from .const import NL_WEIGHT_FACTORS, WEIGHTING_NL


@dataclass(frozen=True, slots=True)
class DegreeDayConfig:
    """Settings that determine how degree days are calculated."""

    base_temperature: float = 18.0
    heating_limit: float = 18.0
    weighting: str = WEIGHTING_NL
    baseline_usage: float = 0.0


@dataclass(slots=True)
class DayRecord:
    """Aggregated values for a single local calendar day."""

    day: date
    mean_temperature: float | None = None
    degree_days: float | None = None
    weighted_degree_days: float | None = None
    usage: float | None = None


@dataclass(slots=True)
class PeriodSummary:
    """Totals for a range of days."""

    start: date
    end: date
    days: int
    days_with_temperature: int
    days_with_usage: int
    mean_temperature: float | None
    degree_days: float
    weighted_degree_days: float
    usage: float
    baseline_usage: float
    heating_usage: float
    usage_per_degree_day: float | None
    usage_per_weighted_degree_day: float | None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat()
        for key, value in data.items():
            if isinstance(value, float):
                data[key] = round(value, 4)
        return data


def weight_factor(day: date, weighting: str) -> float:
    """Return the weighting factor for a day."""
    if weighting == WEIGHTING_NL:
        return NL_WEIGHT_FACTORS[day.month]
    return 1.0


def degree_days(mean_temperature: float, config: DegreeDayConfig) -> float:
    """Return the (unweighted) heating degree days for one day.

    A day counts when its mean outdoor temperature is below the heating
    limit ("stookgrens"); the number of degree days is then the difference
    between the base (indoor reference) temperature and the mean.
    """
    if mean_temperature >= config.heating_limit:
        return 0.0
    return max(0.0, config.base_temperature - mean_temperature)


def build_day_record(
    day: date,
    mean_temperature: float | None,
    usage: float | None,
    config: DegreeDayConfig,
) -> DayRecord:
    """Create a day record and derive its degree days."""
    record = DayRecord(day=day, mean_temperature=mean_temperature, usage=usage)
    if mean_temperature is not None:
        record.degree_days = degree_days(mean_temperature, config)
        record.weighted_degree_days = record.degree_days * weight_factor(
            day, config.weighting
        )
    return record


def iter_days(start: date, end: date) -> Iterable[date]:
    """Yield all days from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def summarize(
    records: Mapping[date, DayRecord],
    start: date,
    end: date,
    config: DegreeDayConfig,
) -> PeriodSummary:
    """Summarise the days from start to end (inclusive).

    An empty range (end before start) gives a summary of zero days.
    """
    days = 0
    temp_days = 0
    usage_days = 0
    temp_total = 0.0
    dd_total = 0.0
    wdd_total = 0.0
    usage_total = 0.0

    for day in iter_days(start, end):
        days += 1
        if (record := records.get(day)) is None:
            continue
        if record.mean_temperature is not None:
            temp_days += 1
            temp_total += record.mean_temperature
            dd_total += record.degree_days or 0.0
            wdd_total += record.weighted_degree_days or 0.0
        if record.usage is not None:
            usage_days += 1
            usage_total += record.usage

    # The baseline (hot water, cooking) is only subtracted for days for
    # which consumption is known.
    baseline_total = config.baseline_usage * usage_days
    heating_usage = max(0.0, usage_total - baseline_total)

    return PeriodSummary(
        start=start,
        end=end,
        days=days,
        days_with_temperature=temp_days,
        days_with_usage=usage_days,
        mean_temperature=temp_total / temp_days if temp_days else None,
        degree_days=dd_total,
        weighted_degree_days=wdd_total,
        usage=usage_total,
        baseline_usage=baseline_total,
        heating_usage=heating_usage,
        usage_per_degree_day=heating_usage / dd_total if dd_total > 0 else None,
        usage_per_weighted_degree_day=(
            heating_usage / wdd_total if wdd_total > 0 else None
        ),
    )


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / old * 100, 2)


def compare(reference: PeriodSummary, other: PeriodSummary) -> dict[str, Any]:
    """Compare two periods.

    ``reference`` is the period you compare against (e.g. last year) and
    ``other`` the period being evaluated (e.g. this year). Besides the raw
    difference, ``other`` is corrected to the weather of ``reference``: the
    usage ``other`` would have had with the reference period's (weighted)
    degree days, given its own usage per weighted degree day.
    """
    corrected: float | None = None
    if other.usage_per_weighted_degree_day is not None:
        corrected = (
            other.usage_per_weighted_degree_day * reference.weighted_degree_days
            + other.baseline_usage
        )

    return {
        "usage_difference": round(other.usage - reference.usage, 4),
        "usage_difference_pct": _pct(other.usage, reference.usage),
        "weighted_degree_days_difference": round(
            other.weighted_degree_days - reference.weighted_degree_days, 4
        ),
        "weighted_degree_days_difference_pct": _pct(
            other.weighted_degree_days, reference.weighted_degree_days
        ),
        "usage_per_weighted_degree_day_difference_pct": _pct(
            other.usage_per_weighted_degree_day,
            reference.usage_per_weighted_degree_day,
        ),
        "weather_corrected_usage": (
            round(corrected, 4) if corrected is not None else None
        ),
        "weather_corrected_difference_pct": _pct(corrected, reference.usage),
    }


def same_day_previous_year(day: date) -> date:
    """Return the same calendar day one year earlier (29 Feb -> 28 Feb)."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def year_start(day: date, start_month: int) -> date:
    """Return the first day of the (possibly shifted) year containing day."""
    year = day.year if day.month >= start_month else day.year - 1
    return date(year, start_month, 1)


def period_key(day: date, group_by: str) -> date:
    """Return the first day of the group a day belongs to."""
    if group_by == "week":
        return day - timedelta(days=day.weekday())
    if group_by == "month":
        return day.replace(day=1)
    if group_by == "year":
        return day.replace(month=1, day=1)
    return day


def group_periods(
    records: Mapping[date, DayRecord],
    start: date,
    end: date,
    group_by: str,
    config: DegreeDayConfig,
) -> list[PeriodSummary]:
    """Summarise the range start..end per day, week, month or year."""
    groups: dict[date, list[date]] = {}
    for day in iter_days(start, end):
        groups.setdefault(period_key(day, group_by), []).append(day)
    return [summarize(records, days[0], days[-1], config) for days in groups.values()]
