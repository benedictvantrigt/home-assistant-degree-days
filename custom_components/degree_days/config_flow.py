"""Config flow for the Degree Days integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowHandler
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BASE_TEMPERATURE,
    CONF_BASELINE_USAGE,
    CONF_CONSUMPTION_SENSOR,
    CONF_HEATING_LIMIT,
    CONF_HISTORY_START,
    CONF_IMPORT_STATISTICS,
    CONF_KNMI_STATION,
    CONF_KNMI_STATION_NAME,
    CONF_LOCATION,
    CONF_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SOURCE,
    CONF_WEIGHTING,
    CONF_YEAR_START_MONTH,
    DEFAULT_BASE_TEMPERATURE,
    DEFAULT_BASELINE_USAGE,
    DEFAULT_HEATING_LIMIT,
    DEFAULT_IMPORT_STATISTICS,
    DEFAULT_WEIGHTING,
    DEFAULT_YEAR_START_MONTH,
    DOMAIN,
    SOURCE_KNMI,
    SOURCE_OPEN_METEO,
    SOURCE_OPTIONS,
    SOURCE_SENSOR,
    WEIGHTING_OPTIONS,
)
from .sources import KnmiSource, KnmiStation, SourceAuthError, SourceError

DEFAULT_NAME = "Degree days"

SOURCE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=SOURCE_OPTIONS,
        translation_key=CONF_TEMPERATURE_SOURCE,
        mode=selector.SelectSelectorMode.LIST,
    )
)
SOURCE_KEYS = (
    CONF_TEMPERATURE_SOURCE,
    CONF_TEMPERATURE_SENSOR,
    CONF_LOCATION,
    CONF_API_KEY,
    CONF_KNMI_STATION,
    CONF_KNMI_STATION_NAME,
)

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


def _unique_id(source: dict[str, Any], consumption: str | None) -> str:
    """Identify an entry by its temperature source and meter."""
    kind = source[CONF_TEMPERATURE_SOURCE]
    if kind == SOURCE_OPEN_METEO:
        location = source[CONF_LOCATION]
        key = f"open_meteo_{location[CONF_LATITUDE]:.3f}_{location[CONF_LONGITUDE]:.3f}"
    elif kind == SOURCE_KNMI:
        key = f"knmi_{source[CONF_KNMI_STATION]}"
    else:
        key = source[CONF_TEMPERATURE_SENSOR]
    return f"{key}_{consumption or ''}"


class _SourceSteps(FlowHandler):
    """Steps to choose the temperature source, shared by both flows."""

    _source: dict[str, Any]
    _api_key: str | None = None
    _stations: list[KnmiStation]

    async def _async_source_done(self) -> ConfigFlowResult:
        raise NotImplementedError

    def _current(self, key: str, default: Any = None) -> Any:
        return default

    async def _async_route_source(self, source: str) -> ConfigFlowResult:
        self._source = {CONF_TEMPERATURE_SOURCE: source}
        if source == SOURCE_OPEN_METEO:
            return await self.async_step_source_open_meteo()
        if source == SOURCE_KNMI:
            return await self.async_step_source_knmi()
        return await self.async_step_source_sensor()

    async def async_step_source_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use an outdoor temperature sensor."""
        if user_input is not None:
            self._source[CONF_TEMPERATURE_SENSOR] = user_input[CONF_TEMPERATURE_SENSOR]
            return await self._async_source_done()
        schema = vol.Schema(
            {vol.Required(CONF_TEMPERATURE_SENSOR): TEMPERATURE_SELECTOR}
        )
        if current := self._current(CONF_TEMPERATURE_SENSOR):
            schema = self.add_suggested_values_to_schema(
                schema, {CONF_TEMPERATURE_SENSOR: current}
            )
        return self.async_show_form(step_id="source_sensor", data_schema=schema)

    async def async_step_source_open_meteo(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use Open-Meteo for a location."""
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            self._source[CONF_LOCATION] = {
                CONF_LATITUDE: round(float(location[CONF_LATITUDE]), 4),
                CONF_LONGITUDE: round(float(location[CONF_LONGITUDE]), 4),
            }
            return await self._async_source_done()
        default = self._current(CONF_LOCATION) or {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
        }
        return self.async_show_form(
            step_id="source_open_meteo",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCATION, default=default
                    ): selector.LocationSelector(
                        selector.LocationSelectorConfig(radius=False)
                    )
                }
            ),
        )

    async def async_step_source_knmi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the KNMI API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            stations, error = await _async_knmi_stations(self, api_key)
            if error:
                errors[CONF_API_KEY] = error
            else:
                self._api_key = api_key
                self._stations = stations
                return await self.async_step_knmi_station()
        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                )
            }
        )
        if current := self._current(CONF_API_KEY):
            schema = self.add_suggested_values_to_schema(
                schema, {CONF_API_KEY: current}
            )
        return self.async_show_form(
            step_id="source_knmi", data_schema=schema, errors=errors
        )

    async def async_step_knmi_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the KNMI station."""
        by_id = {station.station_id: station for station in self._stations}
        if user_input is not None:
            station = by_id[user_input[CONF_KNMI_STATION]]
            self._source.update(
                {
                    CONF_API_KEY: self._api_key,
                    CONF_KNMI_STATION: station.station_id,
                    CONF_KNMI_STATION_NAME: station.name,
                }
            )
            return await self._async_source_done()

        latitude, longitude = self.hass.config.latitude, self.hass.config.longitude

        def distance(station: KnmiStation) -> float:
            value = station.distance_km(latitude, longitude)
            return value if value is not None else float("inf")

        ordered = sorted(self._stations, key=lambda s: (distance(s), s.name))
        options = []
        for station in ordered:
            km = station.distance_km(latitude, longitude)
            label = station.name if km is None else f"{station.name} ({km:.0f} km)"
            options.append(
                selector.SelectOptionDict(value=station.station_id, label=label)
            )
        current = self._current(CONF_KNMI_STATION)
        default = current if current in by_id else ordered[0].station_id
        return self.async_show_form(
            step_id="knmi_station",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_KNMI_STATION, default=default
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )


async def _async_knmi_stations(
    flow: FlowHandler, api_key: str
) -> tuple[list[KnmiStation], str | None]:
    """Check a KNMI key and return the stations, or an error key."""
    source = KnmiSource(async_get_clientsession(flow.hass), api_key, None)
    try:
        stations = await source.async_stations()
    except SourceAuthError:
        return [], "invalid_auth"
    except SourceError:
        return [], "cannot_connect"
    if not stations:
        return [], "no_stations"
    return stations, None


class DegreeDaysConfigFlow(_SourceSteps, ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Degree Days."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._data: dict[str, Any] = {}
        self._source = {}
        self._stations = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a name, the temperature source and the meter."""
        if user_input is not None:
            self._data = {
                CONF_NAME: user_input[CONF_NAME],
                CONF_CONSUMPTION_SENSOR: user_input.get(CONF_CONSUMPTION_SENSOR),
            }
            return await self._async_route_source(user_input[CONF_TEMPERATURE_SOURCE])

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=DEFAULT_NAME
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_TEMPERATURE_SOURCE, default=SOURCE_SENSOR
                    ): SOURCE_SELECTOR,
                    vol.Optional(CONF_CONSUMPTION_SENSOR): CONSUMPTION_SELECTOR,
                }
            ),
        )

    async def _async_source_done(self) -> ConfigFlowResult:
        await self.async_set_unique_id(
            _unique_id(self._source, self._data.get(CONF_CONSUMPTION_SENSOR))
        )
        self._abort_if_unique_id_configured()
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the calculation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _clean_options(user_input)
            if not (errors := _validate_options(options)):
                data = {
                    key: value
                    for key, value in self._data.items()
                    if key != CONF_NAME and value is not None
                }
                return self.async_create_entry(
                    title=self._data[CONF_NAME],
                    data=data,
                    options={**options, **self._source},
                )

        return self.async_show_form(
            step_id="settings",
            data_schema=_options_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Ask for a new KNMI API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter a new KNMI API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            _, error = await _async_knmi_stations(self, api_key)
            if error:
                errors[CONF_API_KEY] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, options={**entry.options, CONF_API_KEY: api_key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return DegreeDaysOptionsFlow()


class DegreeDaysOptionsFlow(_SourceSteps, OptionsFlow):
    """Change the calculation settings or the temperature source."""

    def __init__(self) -> None:
        """Initialise the flow."""
        self._source = {}
        self._stations = []

    def _current(self, key: str, default: Any = None) -> Any:
        merged = {**self.config_entry.data, **self.config_entry.options}
        return merged.get(key, default)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to change."""
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "temperature_source"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the calculation settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _clean_options(user_input)
            if not (errors := _validate_options(options)):
                kept = {
                    key: value
                    for key, value in self.config_entry.options.items()
                    if key in SOURCE_KEYS
                }
                return self.async_create_entry(data={**options, **kept})

        return self.async_show_form(
            step_id="settings",
            data_schema=_options_schema(user_input or dict(self.config_entry.options)),
            errors=errors,
        )

    async def async_step_temperature_source(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the temperature source."""
        if user_input is not None:
            return await self._async_route_source(user_input[CONF_TEMPERATURE_SOURCE])
        return self.async_show_form(
            step_id="temperature_source",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TEMPERATURE_SOURCE,
                        default=self._current(CONF_TEMPERATURE_SOURCE, SOURCE_SENSOR),
                    ): SOURCE_SELECTOR
                }
            ),
        )

    async def _async_source_done(self) -> ConfigFlowResult:
        entry = self.config_entry
        options = {
            key: value for key, value in entry.options.items() if key not in SOURCE_KEYS
        }
        options.update(self._source)
        # Update the unique id together with the options, so the entry is
        # reloaded once, with the new source.
        self.hass.config_entries.async_update_entry(
            entry,
            unique_id=_unique_id(self._source, entry.data.get(CONF_CONSUMPTION_SENSOR)),
            options=options,
        )
        return self.async_create_entry(data=options)
