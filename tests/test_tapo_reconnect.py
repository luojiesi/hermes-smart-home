"""Tapo reconnect path must not depend on the never-committed tools.device_scanner."""
import asyncio
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

PLUG = Path(__file__).resolve().parent.parent


def _tapo():
    spec = importlib.util.spec_from_file_location(
        "smart_home_plugin", PLUG / "__init__.py", submodule_search_locations=[str(PLUG)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["smart_home_plugin"] = pkg
    spec.loader.exec_module(pkg)
    return importlib.import_module("smart_home_plugin.tapo")


def test_no_device_scanner_rescan_left():
    src = (PLUG / "tapo.py").read_text()
    assert "tools.device_scanner import rescan" not in src


def test_connect_failure_rescans_in_tool_and_retries(monkeypatch):
    t = _tapo()
    caches = [{"test plug": "10.0.0.5"}, {"test plug": "10.0.0.9"}]
    monkeypatch.setattr(t, "_load_device_cache", lambda: caches[0])
    scans = []

    async def fake_scan():
        scans.append(1)
        caches[0] = caches[1]

    tried = []

    async def fake_connect(ip):
        tried.append(ip)
        if ip == "10.0.0.5":
            raise OSError("handshake failed")
        class D:
            alias, model, is_on = "Test Plug", "S515", False
            async def update(self): pass
            async def disconnect(self): pass
        return D()

    monkeypatch.setattr(t, "_scan_all_devices", fake_scan)
    monkeypatch.setattr(t, "_connect_device", fake_connect)
    fn = next(f for n, f in inspect.getmembers(t, inspect.iscoroutinefunction) if n.startswith("_do_"))
    asyncio.run(fn("status", "Test Plug"))
    assert scans == [1]
    assert tried == ["10.0.0.5", "10.0.0.9"]
