"""Date entities to choose the periods to compare."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PERIOD_KEYS
from .coordinator import DegreeDaysConfigEntry, DegreeDaysCoordinator
from .entity import DegreeDaysEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DegreeDaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the date entities."""
    async_add_entities(
        DegreeDaysPeriodDate(entry.runtime_data, key) for key in PERIOD_KEYS
    )


class DegreeDaysPeriodDate(DegreeDaysEntity, DateEntity):
    """First or last day of one of the compared periods.

    Shows the dates of the selected preset; changing a date switches the
    comparison to "custom".
    """

    def __init__(self, coordinator: DegreeDaysCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, key)
        self._key = key
        self._attr_translation_key = key

    @property
    def native_value(self) -> date:
        """Return the date."""
        return self.coordinator.data.period_comparison.periods.as_dict()[self._key]

    async def async_set_value(self, value: date) -> None:
        """Change the date."""
        await self.coordinator.async_set_period_date(self._key, value)
