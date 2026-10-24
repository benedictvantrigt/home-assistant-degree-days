"""The bundled dashboard must only reference entities the integration creates."""

from datetime import datetime
from pathlib import Path
import re

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
import yaml

from custom_components.degree_days.const import (
    CONF_CONSUMPTION_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .test_init import TEMP, _import_history

DASHBOARD = Path(__file__).parent.parent / "dashboards" / "degree_days.yaml"
GAS = "sensor.gas_consumption"


@pytest.mark.usefixtures("setup_integration_env")
async def test_dashboard_entities(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """All entities and templates in the dashboard work."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = dt_util.get_default_time_zone()
    freezer.move_to(dt_util.as_utc(datetime(2025, 1, 21, 12, 0, tzinfo=tz)))
    hass.states.async_set(TEMP, "8.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(GAS, "100", {"unit_of_measurement": "m³"})
    _import_history(hass, dt_util.as_utc(datetime(2025, 1, 1, tzinfo=tz)), 20)
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Degree days",
        data={CONF_TEMPERATURE_SENSOR: TEMP, CONF_CONSUMPTION_SENSOR: GAS},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    text = DASHBOARD.read_text()
    yaml.safe_load(text)
    entity_ids = set(re.findall(r"\b(?:sensor|select|date)\.[a-z0-9_]+", text))
    assert entity_ids
    missing = sorted(e for e in entity_ids if hass.states.get(e) is None)
    assert not missing

    markdown = next(
        card["content"]
        for view in yaml.safe_load(text)["views"]
        for card in view["cards"]
        if card["type"] == "markdown"
    )
    rendered = Template(markdown, hass).async_render()
    assert "Period A" in rendered
    assert "One of the periods has no data" in rendered
