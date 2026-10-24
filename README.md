# Degree Days for Home Assistant

A Home Assistant integration that calculates (weighted) heating degree days
from your own outdoor temperature sensor, relates them to your gas or energy
consumption, and lets you compare periods the way
[Mindergas](https://mindergas.nl) does — using the history that is already in
your Home Assistant recorder.

## Features

- **Degree days and weighted degree days** per day, month and year.
  Dutch weighting (*gewogen graaddagen*): Nov–Feb × 1.1, Mar & Oct × 1.0,
  Apr–Sep × 0.8. Base temperature (default 18 °C) and heating limit
  (*stookgrens*) are configurable.
- **Consumption per day/month/year** from any gas or energy meter with
  long-term statistics (DSMR/P1 gas meter, heat pump kWh, …).
- **Usage per weighted degree day** (yesterday, last 30 days, this year,
  previous year), optionally excluding a fixed daily non-heating usage (hot
  water, cooking).
- **Year-to-date comparison with last year, corrected for the weather.**
- **Uses your existing history.** Everything is calculated from the
  recorder's long-term statistics, which Home Assistant keeps indefinitely, so
  results go back as far as your sensors have been recorded (by default the
  last 10 years are read).
- **Degree days as long-term statistics** (`degree_days:<entry>_degree_days`
  and `degree_days:<entry>_weighted_degree_days`), so you can plot and compare
  them per day/week/month/year next to your gas usage in the
  [statistics graph card](https://www.home-assistant.io/dashboards/statistics-graph/).
- **Actions (services) to compare arbitrary periods** and to retrieve the
  data per day, week, month or year.

## Installation

### HACS (custom repository)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/benedictvantrigt/home-assistant-degree-days` with
   type **Integration**.
3. Install **Degree Days** and restart Home Assistant.

### Manual

Copy `custom_components/degree_days` into your `config/custom_components`
folder and restart Home Assistant.

Requires Home Assistant 2025.11 or newer.

## Configuration

**Settings → Devices & services → Add integration → Degree Days**

| Setting | Description |
| --- | --- |
| Outdoor temperature sensor | A temperature sensor with `state_class: measurement` (needed for long-term statistics). A sensor of a weather station near you or your own outdoor sensor. |
| Consumption meter | Optional. A gas/energy meter with `state_class: total` or `total_increasing`. |
| Base temperature | Degree days for a day = base − mean outdoor temperature. 18 °C is the Dutch standard. |
| Heating limit | A day only counts when its mean temperature is below this value. Equal to the base temperature by default. |
| Weighting | *Weighted (Netherlands / Mindergas)* or *Unweighted*. |
| Non-heating usage per day | Subtracted from the daily usage before calculating usage per degree day. |
| First month of the year | Start of the yearly totals: January for calendar years, e.g. July for heating seasons. |
| Read history from | Oldest date to read. Empty = last 10 years. |
| Write degree day statistics | Store degree days as long-term statistics for the statistics graph card. |

Add one entry per meter, e.g. one for gas and one for a heat pump.

## How it is calculated

- The mean temperature of a day is the average of the hourly means of the
  local calendar day, read from the recorder statistics (the KNMI also
  defines the daily mean as the average of 24 hourly values). Today's value
  includes the 5-minute statistics and is updated every 10 minutes.
- Degree days = `max(0, base − T_mean)` if `T_mean < heating limit`, else 0.
- Weighted degree days = degree days × monthly factor.
- Usage per day is the change of the meter's statistics during the local day.
- Usage per weighted degree day = (usage − non-heating usage × days) ÷
  weighted degree days.
- Weather-corrected usage of period B relative to period A =
  usage per weighted degree day (B) × weighted degree days (A) + non-heating
  usage (B). This answers "what would I have used this year with last year's
  weather?".

## Sensors

| Sensor | Notes |
| --- | --- |
| Mean outdoor temperature today / yesterday | °C |
| (Weighted) degree days today / yesterday | today is "so far" |
| Weighted degree days this month / this year / last year to date / previous year | attributes contain the full period summary |
| Usage today / yesterday / this month / this year / last year to date | unit of the meter |
| Usage per weighted degree day yesterday / last 30 days / this year / previous year | e.g. m³/°C·d |
| Weather corrected usage this year | this year's usage at last year's weather (same period) |
| Weather corrected difference with last year | %; negative = you used less than last year, independent of the weather |

The year-on-year comparison (the *last year to date* and *weather corrected*
sensors) uses completed days only: this year up to and including yesterday,
against last year up to the same date. Today's partial day is left out so it
is not compared with a full day.

## Comparing two periods

The integration adds entities to compare any two periods on a dashboard:

| Entity | Purpose |
| --- | --- |
| Comparison (select) | *Last 4 weeks vs previous 4 weeks*, *This month vs same month last year*, *This year vs last year* or *Custom dates* |
| Period A start / end (date) | Reference period, e.g. before a change to your heating |
| Period B start / end (date) | Period to compare, e.g. after the change |
| Comparison weather corrected difference | **The main result**: % more (+) or less (−) gas in period B than period A would have used in B's weather |
| Comparison usage per weighted degree day difference | Same, based on heating usage only (non-heating usage excluded) |
| Comparison usage difference | Raw difference, not weather corrected |
| Comparison usage / weighted degree days / usage per weighted degree day A and B | The underlying values |

The presets end yesterday, so only complete days are compared. Changing a
date switches to *Custom dates*; selecting *Custom dates* again later restores
your own dates. The choice is kept across restarts. The comparison is
calculated from data already in memory, so it updates immediately and does
not query the database. When a period has no data, or an end date is before
its start date, the sensors show *unknown* and the `problem` attribute says
why.

## Dashboard

[`dashboards/degree_days.yaml`](dashboards/degree_days.yaml) is a ready-made
dashboard with a *Compare periods* tab and an *Overview* tab:

1. **Settings → Dashboards → Add dashboard → New dashboard from scratch.**
2. Open it, click the pencil (edit), then **⋮ → Raw configuration editor**.
3. Replace the contents with the file and save.

It assumes the integration entry has the default name *Degree days* and the
gas meter is `sensor.gas_consumption`; otherwise search and replace
`degree_days_` and `sensor.gas_consumption`.

## Actions

### `degree_days.compare_periods`

```yaml
action: degree_days.compare_periods
data:
  config_entry_id: <entry id>
  period_a_start: "2024-01-01"
  period_a_end: "2024-12-31"
  period_b_start: "2025-01-01"
  period_b_end: "2025-12-31"
response_variable: result
```

Returns per period: days, mean temperature, degree days, weighted degree days,
usage, heating usage, usage per (weighted) degree day, and a `comparison`
block with the differences and the weather-corrected usage of period B.

### `degree_days.get_daily_data`

Returns the same summary per `day`, `week`, `month` or `year` for a date
range, e.g. for use in templates or custom cards.

### `degree_days.rebuild_history`

Reads the full history again and rewrites the degree day statistics, e.g.
after importing older meter data.

The actions accept periods of up to about 25 years (9132 days).

## Comparing periods in a dashboard

```yaml
type: statistics-graph
title: Gas vs weighted degree days per month
period: month
stat_types: [change]
chart_type: bar
entities:
  - sensor.gas_meter
  - degree_days:<entry id in lowercase>_weighted_degree_days
```

## Releasing a new version

Set the new `version` in `custom_components/degree_days/manifest.json` and
push it to `main`. The *Release* workflow then creates the tag (e.g.
`v0.2.2`) and a GitHub release, which HACS shows as the installable version.

## Notes

- The degree day statistics are rewritten from scratch each time Home
  Assistant starts or the settings change, and removed when you delete the
  integration entry or turn the option off.

- Results are only as good as the temperature sensor: a sensor in direct sun
  or against a wall will read too high. A nearby official weather station is
  closest to what Mindergas uses.
- The weighting factors are the ones published for the Dutch gas market
  (ACM). Mindergas uses weighted degree days from KNMI station data; small
  differences with Mindergas are expected when your sensor differs from the
  KNMI station.
