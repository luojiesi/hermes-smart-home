"""LG TV DHCP-drift recovery (_rescan_lgtv).

The reconnect path used to import ``tools.device_scanner``, a module that was
never committed, so every connection failure surfaced as
``No module named 'tools.device_scanner'`` and the rescan never ran.
"""

import json

import pytest
import yaml

import importlib, importlib.util, sys
from pathlib import Path
_P = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("smart_home_plugin", _P / "__init__.py", submodule_search_locations=[str(_P)])
_m = importlib.util.module_from_spec(_s); sys.modules["smart_home_plugin"] = _m; _s.loader.exec_module(_m)
lg = importlib.import_module("smart_home_plugin.lgtv")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    conf = tmp_path / "lgtv.yaml"
    keys = tmp_path / "lgtv_keys.json"
    conf.write_text(yaml.safe_dump({"tvs": [
        {"name": "TV Alpha", "ip": "10.0.0.103", "mac": "aa"},
        {"name": "TV Beta", "ip": "10.0.0.135", "mac": "bb"},
    ]}))
    keys.write_text(json.dumps({"10.0.0.103": {"client_key": "K"}}))
    monkeypatch.setattr(lg, "_CONFIG_FILE", conf)
    monkeypatch.setattr(lg, "_get_keys_path", lambda: keys)
    return conf, keys


def test_no_device_scanner_import_left():
    import inspect
    assert "tools.device_scanner import rescan" not in inspect.getsource(lg.lgtv_control)


def test_moved_tv_is_found_and_persisted(cfg, monkeypatch):
    conf, keys = cfg
    # .135 is the other configured TV; .150 is the new address of .103
    monkeypatch.setattr(lg, "_scan_webos_hosts", lambda prefix: ["10.0.0.135", "10.0.0.150"])
    assert lg._rescan_lgtv("10.0.0.103") == "10.0.0.150"
    ips = [t["ip"] for t in yaml.safe_load(conf.read_text())["tvs"]]
    assert ips == ["10.0.0.150", "10.0.0.135"]
    assert json.loads(keys.read_text())["10.0.0.150"] == {"client_key": "K"}


def test_tv_off_returns_none_and_leaves_config(cfg, monkeypatch):
    conf, _ = cfg
    before = conf.read_text()
    monkeypatch.setattr(lg, "_scan_webos_hosts", lambda prefix: [])
    assert lg._rescan_lgtv("10.0.0.103") is None
    assert conf.read_text() == before


def test_ambiguous_returns_none(cfg, monkeypatch):
    monkeypatch.setattr(lg, "_scan_webos_hosts", lambda prefix: ["10.0.0.150", "10.0.0.151"])
    assert lg._rescan_lgtv("10.0.0.103") is None


def test_same_ip_back_online(cfg, monkeypatch):
    monkeypatch.setattr(lg, "_scan_webos_hosts", lambda prefix: ["10.0.0.103"])
    assert lg._rescan_lgtv("10.0.0.103") == "10.0.0.103"
