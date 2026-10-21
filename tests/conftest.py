"""Fixtures for Degree Days tests."""

import pytest


@pytest.fixture
def setup_integration_env(recorder_mock, enable_custom_integrations):
    """Start the recorder before hass and allow custom integrations.

    The recorder fixture must be requested before ``hass`` is created.
    """
    return
