"""The Degree Days integration."""

from __future__ import annotations

from homeassistant.components.recorder import get_instance
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import DegreeDaysConfigEntry, DegreeDaysCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration's services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DegreeDaysConfigEntry) -> bool:
    """Set up Degree Days from a config entry."""
    coordinator = DegreeDaysCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: DegreeDaysConfigEntry
) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: DegreeDaysConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: DegreeDaysConfigEntry) -> None:
    """Remove the degree day statistics written for a deleted entry."""
    get_instance(hass).async_clear_statistics(
        [
            f"{DOMAIN}:{entry.entry_id.lower()}_degree_days",
            f"{DOMAIN}:{entry.entry_id.lower()}_weighted_degree_days",
        ]
    )
