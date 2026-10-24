"""Select entity to choose a period comparison preset."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PRESET_OPTIONS
from .coordinator import DegreeDaysConfigEntry, DegreeDaysCoordinator
from .entity import DegreeDaysEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DegreeDaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select entity."""
    async_add_entities([DegreeDaysPresetSelect(entry.runtime_data)])


class DegreeDaysPresetSelect(DegreeDaysEntity, SelectEntity):
    """Which periods to compare."""

    _attr_translation_key = "comparison_preset"
    _attr_options = PRESET_OPTIONS

    def __init__(self, coordinator: DegreeDaysCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "comparison_preset")

    @property
    def current_option(self) -> str:
        """Return the selected preset."""
        return self.coordinator.preset

    async def async_select_option(self, option: str) -> None:
        """Select a preset."""
        await self.coordinator.async_set_preset(option)
