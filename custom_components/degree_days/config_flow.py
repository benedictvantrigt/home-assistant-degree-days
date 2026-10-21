"""Config flow for the Degree Days integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

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
    DEFAULT_IMPORT_STATISTICS,
    DEFAULT_WEIGHTING,
    DEFAULT_YEAR_START_MONTH,
    DOMAIN,
    WEIGHTING_OPTIONS,
)

DEFAULT_NAME = "Degree days"

TEMPERATURE_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        domain="sensor", device_class=SensorDeviceClass.TEMPERATURE
    )
)
CONSUMPTION_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        domain="sensor",
        device_class=[
            SensorDeviceClass.GAS,
            SensorDeviceClass.ENERGY,
            SensorDeviceClass.VOLUME,
            SensorDeviceClass.WATER,
        ],
    )
)


def _temperature_number(minimum: float, maximum: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=0.1,
            unit_of_measurement="°C",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    history_start = options.get(CONF_HISTORY_START)
    return vol.Schema(
        {
            vol.Required(
                CONF_BASE_TEMPERATURE,
                default=options.get(CONF_BASE_TEMPERATURE, DEFAULT_BASE_TEMPERATURE),
            ): _temperature_number(5, 30),
            vol.Required(
                CONF_HEATING_LIMIT,
                default=options.get(CONF_HEATING_LIMIT, DEFAULT_HEATING_LIMIT),
            ): _temperature_number(5, 30),
            vol.Required(
                CONF_WEIGHTING,
                default=options.get(CONF_WEIGHTING, DEFAULT_WEIGHTING),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=WEIGHTING_OPTIONS, translation_key=CONF_WEIGHTING
                )
            ),
            vol.Required(
                CONF_BASELINE_USAGE,
                default=options.get(CONF_BASELINE_USAGE, DEFAULT_BASELINE_USAGE),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1000, step=0.001, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_YEAR_START_MONTH,
                default=str(
                    options.get(CONF_YEAR_START_MONTH, DEFAULT_YEAR_START_MONTH)
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[str(month) for month in range(1, 13)],
                    translation_key=CONF_YEAR_START_MONTH,
                )
            ),
            vol.Optional(
                CONF_HISTORY_START,
                description={"suggested_value": history_start},
            ): selector.DateSelector(),
            vol.Required(
                CONF_IMPORT_STATISTICS,
                default=options.get(CONF_IMPORT_STATISTICS, DEFAULT_IMPORT_STATISTICS),
            ): selector.BooleanSelector(),
        }
    )


def _clean_options(user_input: dict[str, Any]) -> dict[str, Any]:
    options = dict(user_input)
    options[CONF_YEAR_START_MONTH] = int(options[CONF_YEAR_START_MONTH])
    if not options.get(CONF_HISTORY_START):
        options.pop(CONF_HISTORY_START, None)
    return options


def _validate_options(options: dict[str, Any]) -> dict[str, str]:
    if options[CONF_HEATING_LIMIT] > options[CONF_BASE_TEMPERATURE]:
        return {CONF_HEATING_LIMIT: "heating_limit_above_base"}
    return {}


class DegreeDaysConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Degree Days."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the sensors."""
        if user_input is not None:
            self._async_abort_entries_match(
                {
                    CONF_TEMPERATURE_SENSOR: user_input[CONF_TEMPERATURE_SENSOR],
                    CONF_CONSUMPTION_SENSOR: user_input.get(CONF_CONSUMPTION_SENSOR),
                }
            )
            self._data = user_input
            return await self.async_step_settings()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=DEFAULT_NAME
                    ): selector.TextSelector(),
                    vol.Required(CONF_TEMPERATURE_SENSOR): TEMPERATURE_SELECTOR,
                    vol.Optional(CONF_CONSUMPTION_SENSOR): CONSUMPTION_SELECTOR,
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the calculation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _clean_options(user_input)
            if not (errors := _validate_options(options)):
                data = dict(self._data)
                title = data.pop(CONF_NAME)
                return self.async_create_entry(title=title, data=data, options=options)

        return self.async_show_form(
            step_id="settings",
            data_schema=_options_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return DegreeDaysOptionsFlow()


class DegreeDaysOptionsFlow(OptionsFlow):
    """Change the calculation settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _clean_options(user_input)
            if not (errors := _validate_options(options)):
                return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(user_input or dict(self.config_entry.options)),
            errors=errors,
        )
