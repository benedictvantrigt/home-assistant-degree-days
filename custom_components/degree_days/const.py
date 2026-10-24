"""Constants for the Degree Days integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "degree_days"

CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"
CONF_CONSUMPTION_SENSOR: Final = "consumption_sensor"
CONF_BASE_TEMPERATURE: Final = "base_temperature"
CONF_HEATING_LIMIT: Final = "heating_limit"
CONF_WEIGHTING: Final = "weighting"
CONF_BASELINE_USAGE: Final = "baseline_usage"
CONF_YEAR_START_MONTH: Final = "year_start_month"
CONF_HISTORY_START: Final = "history_start"
CONF_IMPORT_STATISTICS: Final = "import_statistics"

WEIGHTING_NONE: Final = "none"
WEIGHTING_NL: Final = "nl"
WEIGHTING_OPTIONS: Final = [WEIGHTING_NL, WEIGHTING_NONE]

DEFAULT_BASE_TEMPERATURE: Final = 18.0
DEFAULT_HEATING_LIMIT: Final = 18.0
DEFAULT_WEIGHTING: Final = WEIGHTING_NL
DEFAULT_BASELINE_USAGE: Final = 0.0
DEFAULT_YEAR_START_MONTH: Final = 1
DEFAULT_HISTORY_YEARS: Final = 10
DEFAULT_IMPORT_STATISTICS: Final = True

# Monthly weighting factors for "gewogen graaddagen" as used in the
# Netherlands (ACM / energy suppliers / Mindergas): Nov-Feb 1.1,
# Mar & Oct 1.0, Apr-Sep 0.8. They correct for solar gain.
NL_WEIGHT_FACTORS: Final[dict[int, float]] = {
    1: 1.1,
    2: 1.1,
    3: 1.0,
    4: 0.8,
    5: 0.8,
    6: 0.8,
    7: 0.8,
    8: 0.8,
    9: 0.8,
    10: 1.0,
    11: 1.1,
    12: 1.1,
}

UPDATE_INTERVAL: Final = timedelta(minutes=10)
# Number of past days re-read from the recorder on every update, so late
# statistics (e.g. a meter that reports with a delay) are picked up.
REFRESH_DAYS: Final = 3

UNIT_DEGREE_DAYS: Final = "°C·d"

SERVICE_COMPARE_PERIODS: Final = "compare_periods"
SERVICE_GET_DAILY_DATA: Final = "get_daily_data"
SERVICE_REBUILD_HISTORY: Final = "rebuild_history"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_PERIOD_A_START: Final = "period_a_start"
ATTR_PERIOD_A_END: Final = "period_a_end"
ATTR_PERIOD_B_START: Final = "period_b_start"
ATTR_PERIOD_B_END: Final = "period_b_end"
ATTR_START: Final = "start"
ATTR_END: Final = "end"
ATTR_GROUP_BY: Final = "group_by"

GROUP_BY_OPTIONS: Final = ["day", "week", "month", "year"]

# Period comparison presets. All presets end yesterday, so complete days are
# compared. "custom" uses the dates set on the date entities.
PRESET_CUSTOM: Final = "custom"
PRESET_LAST_4_WEEKS: Final = "last_4_weeks"
PRESET_MONTH_VS_LAST_YEAR: Final = "month_vs_last_year"
PRESET_YEAR_VS_LAST_YEAR: Final = "year_vs_last_year"
PRESET_OPTIONS: Final = [
    PRESET_CUSTOM,
    PRESET_LAST_4_WEEKS,
    PRESET_MONTH_VS_LAST_YEAR,
    PRESET_YEAR_VS_LAST_YEAR,
]
DEFAULT_PRESET: Final = PRESET_LAST_4_WEEKS

PERIOD_A_START: Final = "period_a_start"
PERIOD_A_END: Final = "period_a_end"
PERIOD_B_START: Final = "period_b_start"
PERIOD_B_END: Final = "period_b_end"
PERIOD_KEYS: Final = [PERIOD_A_START, PERIOD_A_END, PERIOD_B_START, PERIOD_B_END]

STORAGE_VERSION: Final = 1

# Longest date range a service call may request (about 25 years).
MAX_RANGE_DAYS: Final = 9132
