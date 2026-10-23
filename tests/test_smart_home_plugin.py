"""smart-home plugin tests: real plugin registration, profile-safe paths, dependency gate."""
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

PLUG = Path(__file__).resolve().parent.parent
NAMES = ("tesla", "tapo", "lgtv", "sonos", "rheem")


def _load_pkg():
    spec = importlib.util.spec_from_file_location(
        "smart_home_plugin", PLUG / "__init__.py", submodule_search_locations=[str(PLUG)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["smart_home_plugin"] = pkg
    spec.loader.exec_module(pkg)
    return pkg


@pytest.fixture
def pkg():
    return _load_pkg()


def test_register_calls_ctx_for_all_five(pkg):
    calls = []

    class Ctx:
        def register_tool(self, **kw):
            calls.append(kw)

    pkg.register(Ctx())
    assert sorted(c["name"] for c in calls) == sorted(NAMES)
    for c in calls:
        assert c["toolset"] == c["name"]
        assert c["schema"]["name"] == c["name"]
        assert callable(c["handler"]) and callable(c["check_fn"])


def test_register_kwargs_accepted_by_real_plugin_context(pkg):
    """TOOL_SPEC keys must all be parameters of the real PluginContext.register_tool."""
    import inspect
    from hermes_cli.plugins import PluginContext
    allowed = set(inspect.signature(PluginContext.register_tool).parameters) - {"self"}
    for n in NAMES:
        spec = importlib.import_module(f"smart_home_plugin.{n}").TOOL_SPEC
        assert set(spec) <= allowed, (n, set(spec) - allowed)


def test_no_hardcoded_hermes_home():
    for n in NAMES:
        src = (PLUG / f"{n}.py").read_text()
        assert "~/.hermes" not in src, n
        assert 'Path.home() / ".hermes' not in src, n


@pytest.mark.parametrize("n", ["tesla", "tapo", "lgtv", "sonos"])
def test_config_dir_follows_hermes_home(n, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    sys.modules.pop(f"smart_home_plugin.{n}", None)
    _load_pkg()
    mod = importlib.import_module(f"smart_home_plugin.{n}")
    assert mod._CONFIG_DIR == tmp_path / "smart_home"


@pytest.mark.parametrize("n", NAMES)
def test_gate_false_when_sdk_missing(n, pkg, monkeypatch):
    mod = importlib.import_module(f"smart_home_plugin.{n}")
    monkeypatch.setattr(mod, "_deps_present", lambda: False)
    assert mod._available() is False


def test_gate_checks_the_right_sdk(pkg):
    want = {"tesla": "requests", "tapo": "kasa", "lgtv": "pywebostv", "sonos": "soco", "rheem": "pyeconet"}
    for n, m in want.items():
        src = (PLUG / f"{n}.py").read_text()
        assert repr([m]) in src, n
