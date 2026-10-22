"""Services for the Degree Days integration."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .calculations import compare, group_periods
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END,
    ATTR_GROUP_BY,
    ATTR_PERIOD_A_END,
    ATTR_PERIOD_A_START,
    ATTR_PERIOD_B_END,
    ATTR_PERIOD_B_START,
    ATTR_START,
    DOMAIN,
    GROUP_BY_OPTIONS,
    MAX_RANGE_DAYS,
    SERVICE_COMPARE_PERIODS,
    SERVICE_GET_DAILY_DATA,
    SERVICE_REBUILD_HISTORY,
)
from .coordinator import DegreeDaysCoordinator

COMPARE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_PERIOD_A_START): cv.date,
        vol.Required(ATTR_PERIOD_A_END): cv.date,
        vol.Required(ATTR_PERIOD_B_START): cv.date,
        vol.Required(ATTR_PERIOD_B_END): cv.date,
    }
)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_START): cv.date,
        vol.Required(ATTR_END): cv.date,
        vol.Optional(ATTR_GROUP_BY, default="day"): vol.In(GROUP_BY_OPTIONS),
    }
)

REBUILD_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> DegreeDaysCoordinator:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"entry_id": entry_id},
        )
    return entry.runtime_data


def _check_range(start: date, end: date) -> None:
    if end < start:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_range",
            translation_placeholders={"start": str(start), "end": str(end)},
        )
    if (end - start).days >= MAX_RANGE_DAYS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="range_too_long",
            translation_placeholders={"days": str(MAX_RANGE_DAYS)},
        )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""

    async def compare_periods(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        a_start, a_end = call.data[ATTR_PERIOD_A_START], call.data[ATTR_PERIOD_A_END]
        b_start, b_end = call.data[ATTR_PERIOD_B_START], call.data[ATTR_PERIOD_B_END]
        _check_range(a_start, a_end)
        _check_range(b_start, b_end)
        period_a = coordinator.summarize(a_start, a_end)
        period_b = coordinator.summarize(b_start, b_end)
        return {
            "unit": coordinator.usage_unit,
            "period_a": period_a.as_dict(),
            "period_b": period_b.as_dict(),
            "comparison": compare(period_a, period_b),
        }

    async def get_daily_data(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        start, end = call.data[ATTR_START], call.data[ATTR_END]
        _check_range(start, end)
        periods: list[dict[str, Any]] = [
            summary.as_dict()
            for summary in group_periods(
                coordinator.records,
                start,
                end,
                call.data[ATTR_GROUP_BY],
                coordinator.config,
            )
        ]
        return {"unit": coordinator.usage_unit, "periods": periods}

    async def rebuild_history(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        await coordinator.async_rebuild_history()

    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPARE_PERIODS,
        compare_periods,
        schema=COMPARE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DAILY_DATA,
        get_daily_data,
        schema=DATA_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REBUILD_HISTORY,
        rebuild_history,
        schema=REBUILD_SCHEMA,
    )
