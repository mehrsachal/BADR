"""Shared fixtures: one offscreen QgsApplication for the whole test session."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(scope="session")
def qgis_app():
    from badr.app import init_qgis
    app = init_qgis(gui=True)
    yield app


@pytest.fixture()
def operation(qgis_app, tmp_path):
    from badr.core.store import Operation
    return Operation(str(tmp_path / "op"), "Test op")
