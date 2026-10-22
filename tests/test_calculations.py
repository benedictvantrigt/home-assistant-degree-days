"""Tests for the pure calculations."""

from datetime import date

import pytest

from custom_components.degree_days.calculations import (
    DegreeDayConfig,
    build_day_record,
    compare,
    degree_days,
    group_periods,
    same_day_previous_year,
    summarize,
    weight_factor,
    year_start,
)

CONFIG = DegreeDayConfig()


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [(18.0, 0.0), (20.0, 0.0), (17.5, 0.5), (3.2, 14.8), (-5.0, 23.0)],
)
def test_degree_days(temperature: float, expected: float) -> None:
    """Degree days are base minus mean below the heating limit."""
    assert degree_days(temperature, CONFIG) == pytest.approx(expected)


def test_heating_limit() -> None:
    """Days at or above the heating limit do not count."""
    config = DegreeDayConfig(base_temperature=18, heating_limit=15.5)
    assert degree_days(16.0, config) == 0.0
    assert degree_days(15.0, config) == pytest.approx(3.0)


@pytest.mark.parametrize(
    ("month", "factor"),
    [(1, 1.1), (2, 1.1), (3, 1.0), (4, 0.8), (9, 0.8), (10, 1.0), (11, 1.1), (12, 1.1)],
)
def test_nl_weighting(month: int, factor: float) -> None:
    """Dutch weighting factors per month."""
    assert weight_factor(date(2025, month, 1), "nl") == factor
    assert weight_factor(date(2025, month, 1), "none") == 1.0


def test_summary_and_baseline() -> None:
    """Summaries subtract the baseline before dividing by degree days."""
    config = DegreeDayConfig(baseline_usage=0.5)
    records = {
        date(2025, 1, 1): build_day_record(date(2025, 1, 1), 8.0, 5.5, config),
        date(2025, 1, 2): build_day_record(date(2025, 1, 2), 13.0, 3.0, config),
    }
    summary = summarize(records, date(2025, 1, 1), date(2025, 1, 3), config)
    assert summary.days == 3
    assert summary.days_with_usage == 2
    assert summary.degree_days == pytest.approx(15.0)
    assert summary.weighted_degree_days == pytest.approx(16.5)
    assert summary.usage == pytest.approx(8.5)
    assert summary.heating_usage == pytest.approx(7.5)
    assert summary.usage_per_weighted_degree_day == pytest.approx(7.5 / 16.5)
    assert summary.mean_temperature == pytest.approx(10.5)


def test_compare_weather_correction() -> None:
    """A colder reference period raises the weather corrected usage."""
    ref = {d: build_day_record(d, 5.0, 2.6, CONFIG) for d in [date(2024, 1, 1)]}
    cur = {d: build_day_record(d, 10.0, 1.6, CONFIG) for d in [date(2025, 1, 1)]}
    a = summarize(ref, date(2024, 1, 1), date(2024, 1, 1), CONFIG)
    b = summarize(cur, date(2025, 1, 1), date(2025, 1, 1), CONFIG)
    result = compare(a, b)
    # Same efficiency: 2.6 / (13*1.1) vs 1.6 / (8*1.1) -> 0.1818 vs 0.1818.
    assert result["weather_corrected_usage"] == pytest.approx(2.6, rel=1e-3)
    assert result["weather_corrected_difference_pct"] == pytest.approx(0.0, abs=0.1)
    assert result["usage_difference_pct"] == pytest.approx(-38.46, abs=0.01)


def test_dates() -> None:
    """Date helpers."""
    assert same_day_previous_year(date(2024, 2, 29)) == date(2023, 2, 28)
    assert year_start(date(2025, 3, 1), 7) == date(2024, 7, 1)
    assert year_start(date(2025, 8, 1), 7) == date(2025, 7, 1)


def test_group_periods() -> None:
    """Grouping per month."""
    records = {
        d: build_day_record(d, 10.0, 1.0, CONFIG)
        for d in [date(2025, 1, 31), date(2025, 2, 1), date(2025, 2, 2)]
    }
    groups = group_periods(
        records, date(2025, 1, 31), date(2025, 2, 2), "month", CONFIG
    )
    assert [g.start for g in groups] == [date(2025, 1, 31), date(2025, 2, 1)]
    assert [g.usage for g in groups] == [1.0, 2.0]


def test_empty_range() -> None:
    """An end before the start is an empty period, not a reversed one.

    On 1 January "this year up to yesterday" is such a range.
    """
    records = {
        d: build_day_record(d, 5.0, 2.0, CONFIG)
        for d in [date(2024, 12, 31), date(2025, 1, 1)]
    }
    summary = summarize(records, date(2025, 1, 1), date(2024, 12, 31), CONFIG)
    assert summary.days == 0
    assert summary.usage == 0.0
    assert summary.usage_per_weighted_degree_day is None
