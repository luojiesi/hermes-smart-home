"""
TP-Link Tapo smart home device control tool for Hermes.

Controls Tapo smart plugs/switches on the local network
via python-kasa using KLAP protocol. Requires TAPO_EMAIL and TAPO_PASSWORD.
Device IPs and config are loaded from config file.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import yaml

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_CONFIG_DIR = get_hermes_home() / "smart_home"
_CONFIG_FILE = _CONFIG_DIR / "tapo.yaml"


def _load_config() -> dict:
    """Load tool config from YAML file."""
    if not _CONFIG_FILE.exists():
        logger.warning("Tapo config not found: %s", _CONFIG_FILE)
        return {}
    try:
        with open(_CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_known_ips() -> list:
    """Get known device IPs from config."""
    return _load_config().get("known_ips", [])


def _get_device_cache_path() -> Path:
    """Get device cache path from config."""
    config = _load_config()
    cache_path = config.get("device_cache", str(_CONFIG_DIR / "tapo_devices.json"))
    return Path(cache_path).expanduser()


def _check_tapo_reqs() -> bool:
    """Check if Tapo credentials are available."""
    email, password = _get_credentials()
    return bool(email and password)


def _get_credentials():
    """Tapo credentials via Hermes's profile-aware secret store (catalog rule 11)."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:  # older Hermes
        get_secret = lambda k, d=None: os.getenv(k, d)  # noqa: E731
    try:
        return get_secret("TAPO_EMAIL") or "", get_secret("TAPO_PASSWORD") or ""
    except Exception:  # e.g. UnscopedSecretError under multiplexing: fail closed
        return "", ""


def _load_device_cache():
    """Load cached device name -> IP mapping from global cache."""
    cache_path = _get_device_cache_path()
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass
    return {}


def _save_device_cache(cache):
    """Save device name -> IP mapping to global cache."""
    cache_path = _get_device_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2))


def _find_device_ip(name: str, cache: dict) -> str | None:
    """Find device IP by name (case-insensitive, partial match)."""
    name_lower = name.lower().strip()
    # Exact match first
    for alias, ip in cache.items():
        if alias.lower() == name_lower:
            return ip
    # Partial match
    for alias, ip in cache.items():
        if name_lower in alias.lower():
            return ip
    return None


async def _connect_device(ip: str):
    """Connect to a Tapo device by IP."""
    from kasa import Discover, Credentials
    email, password = _get_credentials()
    dev = await Discover.discover_single(
        ip,
        credentials=Credentials(email, password),
        timeout=10,
    )
    await dev.update()
    return dev


async def _scan_all_devices():
    """Scan all known IPs and return device info + update cache."""
    known_ips = _get_known_ips()
    cache = {}
    devices = []
    for ip in known_ips:
        try:
            dev = await _connect_device(ip)
            cache[dev.alias] = ip
            info = {
                "name": dev.alias,
                "ip": ip,
                "model": dev.model,
                "is_on": dev.is_on,
            }
            if hasattr(dev, "children") and dev.children:
                info["outlets"] = [
                    {"name": c.alias, "is_on": c.is_on} for c in dev.children
                ]
            devices.append(info)
        except Exception as e:
            devices.append({"ip": ip, "error": str(e)})
    _save_device_cache(cache)
    return devices


async def _do_action(action: str, device_name: str = None, param: str = None):
    """Execute a Tapo device action."""

    if action == "list":
        devices = await _scan_all_devices()
        return {"success": True, "devices": devices}

    if action == "scan":
        devices = await _scan_all_devices()
        online = [d for d in devices if "error" not in d]
        offline = [d for d in devices if "error" in d]
        return {
            "success": True,
            "online": len(online),
            "offline": len(offline),
            "devices": devices,
        }

    # All other actions need a device name
    if not device_name:
        return {"error": "device_name is required for this action"}

    cache = _load_device_cache()
    ip = _find_device_ip(device_name, cache)

    if not ip:
        # Try global rescan to refresh cache
        await _scan_all_devices()
        cache = _load_device_cache()
        ip = _find_device_ip(device_name, cache)
        if not ip:
            return {"error": f"Device '{device_name}' not found. Use action=list to see all devices."}

    try:
        dev = await _connect_device(ip)
    except Exception as first_err:
        # IP might have changed (or a transient handshake failure): rescan the
        # subnet in-tool and retry once. tools.device_scanner never existed.
        logger.info("tapo: connect to %s (%s) failed: %s; rescanning", device_name, ip, first_err)
        try:
            await _scan_all_devices()
            cache = _load_device_cache()
            ip = _find_device_ip(device_name, cache)
            if not ip:
                return {"error": f"Device '{device_name}' not reachable after rescan."}
            dev = await _connect_device(ip)
        except Exception as e:
            return {"error": f"Cannot connect to '{device_name}': {e}"}

    if action == "status":
        info = {
            "name": dev.alias,
            "ip": ip,
            "model": dev.model,
            "is_on": dev.is_on,
        }
        if hasattr(dev, "children") and dev.children:
            info["outlets"] = [
                {"name": c.alias, "is_on": c.is_on} for c in dev.children
            ]
        return {"success": True, **info}

    elif action == "on":
        await dev.turn_on()
        return {"success": True, "message": f"{dev.alias} turned ON"}

    elif action == "off":
        await dev.turn_off()
        return {"success": True, "message": f"{dev.alias} turned OFF"}

    elif action == "toggle":
        if dev.is_on:
            await dev.turn_off()
            return {"success": True, "message": f"{dev.alias} toggled OFF"}
        else:
            await dev.turn_on()
            return {"success": True, "message": f"{dev.alias} toggled ON"}

    else:
        return {"error": f"Unknown action: {action}"}


def tapo_control(action: str, device_name: str = None, param: str = None) -> str:
    """Sync wrapper for async Tapo control."""
    try:
        result = asyncio.run(_do_action(action, device_name, param))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("Tapo tool error: %s", e, exc_info=True)
        return json.dumps({"error": str(e)})


TAPO_SCHEMA = {
    "name": "tapo",
    "description": (
        "Control TP-Link Tapo smart switches/plugs on the local network. "
        "Actions: list (scan & show all devices), status (single device state), "
        "on (turn on), off (turn off), toggle. "
        "device_name: partial match OK. Use action=list to discover available devices."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "status", "on", "off", "toggle"],
                "description": "Action to perform",
            },
            "device_name": {
                "type": "string",
                "description": "Device name (partial match supported). Required for status/on/off/toggle.",
            },
        },
        "required": ["action"],
    },
}


def _deps_present() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ['kasa'])


def _available() -> bool:
    """Expose the tool only when its SDK is importable AND its config/credentials exist."""
    return _deps_present() and _check_tapo_reqs()


TOOL_SPEC = dict(
    name="tapo",
    toolset="tapo",
    schema=TAPO_SCHEMA,
    handler=lambda args, **kw: tapo_control(
        action=args["action"],
        device_name=args.get("device_name"),
        param=args.get("param"),
    ),
    check_fn=_available,
    requires_env=["TAPO_EMAIL", "TAPO_PASSWORD"],
    emoji="💡",
)
