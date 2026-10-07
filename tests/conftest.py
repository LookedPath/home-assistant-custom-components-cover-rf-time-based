"""Shared Home Assistant test setup."""

import pytest


@pytest.fixture(autouse=True)
def enable_custom_components(enable_custom_integrations):
    """Allow this repository's custom integration to load."""
    yield
