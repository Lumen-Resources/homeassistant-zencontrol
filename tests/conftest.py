"""Test setup — make the `tpi` protocol package importable without Home Assistant.

The tpi/ package has no HA dependency, but its parent directory contains HA
platform modules (select.py, sensor.py, …) that would shadow stdlib modules if
the directory were put on sys.path. Instead, load the tpi package explicitly
by file location and register it in sys.modules.
"""
import importlib.util
import sys
from pathlib import Path

_TPI_DIR = Path(__file__).parent.parent / "custom_components" / "zencontrol" / "tpi"

_spec = importlib.util.spec_from_file_location(
    "tpi",
    _TPI_DIR / "__init__.py",
    submodule_search_locations=[str(_TPI_DIR)],
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["tpi"] = _module
_spec.loader.exec_module(_module)
