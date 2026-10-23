"""smart-home — Tesla / Tapo / LG TV / Sonos / Rheem control tools as a Hermes plugin.

Each sibling module exports ``TOOL_SPEC`` (registry.register kwargs). Config and
state live under ``get_hermes_home() / "smart_home"`` so profiles stay isolated.
A tool is only exposed when its vendor SDK is importable and its config exists.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MODULES = ("tesla", "tapo", "lgtv", "sonos", "rheem")


def register(ctx) -> None:
    import importlib
    for name in _MODULES:
        try:
            mod = importlib.import_module(f".{name}", __name__)
            ctx.register_tool(**mod.TOOL_SPEC)
        except Exception:
            logger.warning("smart-home: failed to register %s", name, exc_info=True)
