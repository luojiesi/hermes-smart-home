"""
LG webOS TV control tool for Hermes.

Controls LG TVs on the local network via PyWebOSTV.
TV names, IPs, and MAC addresses are loaded from config file.
"""

import json
import logging
from pathlib import Path

import yaml

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_CONFIG_DIR = get_hermes_home() / "smart_home"
_CONFIG_FILE = _CONFIG_DIR / "lgtv.yaml"


def _load_config() -> dict:
    """Load tool config from YAML file."""
    if not _CONFIG_FILE.exists():
        logger.warning("LGTV config not found: %s", _CONFIG_FILE)
        return {}
    try:
        with open(_CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_devices() -> dict:
    """Build device dict from config."""
    config = _load_config()
    devices = {}
    for tv in config.get("tvs", []):
        devices[tv["name"]] = {"ip": tv["ip"], "mac": tv.get("mac", "")}
    return devices


def _get_keys_path() -> Path:
    """Get the pairing keys file path from config."""
    config = _load_config()
    keys_file = config.get("keys_file", str(_CONFIG_DIR / "lgtv_keys.json"))
    return Path(keys_file).expanduser()


def _check_lgtv_reqs() -> bool:
    try:
        from pywebostv.connection import WebOSClient
        config = _load_config()
        if not config.get("tvs"):
            return False
        return True
    except ImportError:
        return False


def _load_keys() -> dict:
    key_path = _get_keys_path()
    if key_path.exists():
        try:
            return json.loads(key_path.read_text())
        except Exception:
            pass
    return {}


def _save_keys(keys: dict):
    key_path = _get_keys_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(keys, indent=2))


def _get_client(ip: str):
    """Connect and register with the TV, using cached key if available."""
    from pywebostv.connection import WebOSClient

    keys = _load_keys()
    store = {}
    if ip in keys:
        store = {"client_key": keys[ip]}

    client = WebOSClient(ip, secure=True)
    client.connect()
    for status in client.register(store):
        if status == WebOSClient.PROMPTED:
            logger.info("Pairing prompt shown on TV at %s", ip)
        elif status == WebOSClient.REGISTERED:
            if "client_key" in store:
                keys[ip] = store["client_key"]
                _save_keys(keys)
    return client


def _probe_3001(ip: str, timeout: float = 0.6) -> bool:
    import socket
    try:
        with socket.create_connection((ip, 3001), timeout=timeout):
            return True
    except OSError:
        return False


def _scan_webos_hosts(subnet_prefix: str) -> list:
    """Return IPs in ``subnet_prefix.1-254`` with the webOS SSAP port open."""
    from concurrent.futures import ThreadPoolExecutor
    ips = [f"{subnet_prefix}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as ex:
        hits = list(ex.map(_probe_3001, ips))
    return [ip for ip, ok in zip(ips, hits) if ok]


def _rescan_lgtv(dead_ip: str):
    """Find where a configured TV moved after a DHCP change.

    Replaces the never-committed ``tools.device_scanner.rescan`` (the import
    always failed, so the reconnect path was dead). Scans the /24 for hosts
    answering on 3001; an IP not already claimed by another configured TV is
    taken as the new address of ``dead_ip``. lgtv.yaml and the per-IP client
    key are updated so the next call connects directly without a pairing
    prompt (webOS binds the key to the TV, not the IP). Returns the new IP or
    None when nothing new answers (TV off) or the match is ambiguous.
    """
    if not dead_ip or dead_ip.count(".") != 3:
        return None
    config = _load_config()
    tvs = config.get("tvs") or []
    claimed = {t.get("ip") for t in tvs if t.get("ip") and t.get("ip") != dead_ip}
    candidates = [ip for ip in _scan_webos_hosts(dead_ip.rsplit(".", 1)[0])
                  if ip not in claimed]
    if dead_ip in candidates:
        return dead_ip
    if len(candidates) != 1:
        if candidates:
            logger.warning("LGTV rescan ambiguous for %s: %s", dead_ip, candidates)
        return None
    new_ip = candidates[0]
    for t in tvs:
        if t.get("ip") == dead_ip:
            t["ip"] = new_ip
    try:
        with open(_CONFIG_FILE, "w") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
        keys = _load_keys()
        if dead_ip in keys and new_ip not in keys:
            keys[new_ip] = keys[dead_ip]
            _get_keys_path().write_text(json.dumps(keys, indent=2))
    except Exception as e:
        logger.warning("LGTV rescan: could not persist %s -> %s: %s", dead_ip, new_ip, e)
    logger.info("LGTV rescan: %s moved to %s", dead_ip, new_ip)
    return new_ip


def _find_tv(name: str = None) -> dict:
    """Find TV by name (partial match). Checks global cache first, falls back to config."""
    devices = _get_devices()
    
    if not name:
        return list(devices.values())[0] if devices else {"ip": "", "mac": ""}
    name_lower = name.lower().strip()
    for alias, info in devices.items():
        if name_lower in alias.lower():
            return info
    return list(devices.values())[0] if devices else {"ip": "", "mac": ""}


def lgtv_control(action: str, tv_name: str = None, param: str = None) -> str:
    try:
        from pywebostv.controls import (
            MediaControl, SystemControl, ApplicationControl,
            SourceControl, InputControl,
        )

        devices = _get_devices()

        if action == "list":
            return json.dumps({"success": True, "tvs": [
                {"name": name, **info} for name, info in devices.items()
            ]})

        tv = _find_tv(tv_name)
        ip = tv["ip"]

        if action == "wake":
            from wakeonlan import send_magic_packet
            mac = tv.get("mac", "")
            if not mac:
                return json.dumps({"error": "No MAC address configured for this TV"})
            send_magic_packet(mac)
            return json.dumps({"success": True, "message": f"Wake-on-LAN sent to {mac}"})

        # All other actions need a WebSocket connection (TV must be on)
        try:
            client = _get_client(ip)
        except Exception:
            # IP may have changed, try rescan
            try:
                new_ip = _rescan_lgtv(ip)
                if not new_ip:
                    return json.dumps({
                        "error": f"Cannot connect to TV at {ip}; no other LG TV "
                                 "answering on port 3001 (TV likely off or asleep)."
                    })
                ip = new_ip
                client = _get_client(ip)
            except Exception as e:
                return json.dumps({"error": f"Cannot connect to TV: {e}"})
        system = SystemControl(client)
        media = MediaControl(client)

        if action == "status":
            vol = media.get_volume()
            # Handle both flat and nested volumeStatus response formats
            vs = vol.get("volumeStatus", vol)
            return json.dumps({
                "success": True,
                "ip": ip,
                "volume": vs.get("volume"),
                "muted": vs.get("muteStatus", vs.get("muted")),
            })

        elif action == "power_off":
            system.power_off()
            return json.dumps({"success": True, "message": "TV powered off"})

        elif action == "screen_off":
            system.screen_off()
            return json.dumps({"success": True, "message": "Screen turned off (audio continues)"})

        elif action == "screen_on":
            system.screen_on()
            return json.dumps({"success": True, "message": "Screen turned on"})

        elif action == "volume":
            if not param:
                vol = media.get_volume()
                vs = vol.get("volumeStatus", vol)
                return json.dumps({"success": True, "volume": vs.get("volume"), "muted": vs.get("muteStatus", vs.get("muted"))})
            media.set_volume(int(param))
            return json.dumps({"success": True, "message": f"Volume set to {param}"})

        elif action == "volume_up":
            media.volume_up()
            return json.dumps({"success": True, "message": "Volume up"})

        elif action == "volume_down":
            media.volume_down()
            return json.dumps({"success": True, "message": "Volume down"})

        elif action == "mute":
            media.mute(True)
            return json.dumps({"success": True, "message": "Muted"})

        elif action == "unmute":
            media.mute(False)
            return json.dumps({"success": True, "message": "Unmuted"})

        elif action == "input":
            if not param:
                source = SourceControl(client)
                sources = source.list_sources()
                return json.dumps({"success": True, "sources": [str(s) for s in sources]})
            source = SourceControl(client)
            sources = source.list_sources()
            for s in sources:
                if param.lower() in str(s).lower():
                    source.set_source(s)
                    return json.dumps({"success": True, "message": f"Switched to {s}"})
            return json.dumps({"error": f"Source '{param}' not found. Available: {[str(s) for s in sources]}"})

        elif action == "app":
            app_ctrl = ApplicationControl(client)
            if not param:
                apps = app_ctrl.list_apps()
                app_names = [{"title": a["title"], "id": a["id"]} for a in apps]
                return json.dumps({"success": True, "apps": app_names}, ensure_ascii=False)
            apps = app_ctrl.list_apps()
            for a in apps:
                if param.lower() in a["title"].lower() or param.lower() == a["id"].lower():
                    app_ctrl.launch(a)
                    return json.dumps({"success": True, "message": f"Launched {a['title']}"})
            return json.dumps({"error": f"App '{param}' not found"})

        elif action == "play":
            media.play()
            return json.dumps({"success": True, "message": "Play"})

        elif action == "pause":
            media.pause()
            return json.dumps({"success": True, "message": "Paused"})

        elif action == "stop":
            media.stop()
            return json.dumps({"success": True, "message": "Stopped"})

        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        logger.error("LG TV tool error: %s", e, exc_info=True)
        return json.dumps({"error": str(e)})


LGTV_SCHEMA = {
    "name": "lgtv",
    "description": (
        "Control LG webOS TVs on the local network. "
        "Actions: list, status, wake (WOL power on), power_off, "
        "screen_off, screen_on, "
        "volume (param=0-100, omit to get current), volume_up, volume_down, "
        "mute, unmute, "
        "input (param=source name like 'HDMI 1', omit to list), "
        "app (param=app name like 'YouTube'/'Netflix', omit to list), "
        "play, pause, stop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list", "status", "wake", "power_off",
                    "screen_off", "screen_on",
                    "volume", "volume_up", "volume_down", "mute", "unmute",
                    "input", "app", "play", "pause", "stop",
                ],
                "description": "Action to perform on the TV",
            },
            "tv_name": {
                "type": "string",
                "description": "TV name (partial match). Defaults to first configured TV.",
            },
            "param": {
                "type": "string",
                "description": "Extra parameter — volume level, input name, or app name",
            },
        },
        "required": ["action"],
    },
}


def _deps_present() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ['pywebostv'])


def _available() -> bool:
    """Expose the tool only when its SDK is importable AND its config/credentials exist."""
    return _deps_present() and _check_lgtv_reqs()


TOOL_SPEC = dict(
    name="lgtv",
    toolset="lgtv",
    schema=LGTV_SCHEMA,
    handler=lambda args, **kw: lgtv_control(
        action=args["action"],
        tv_name=args.get("tv_name"),
        param=args.get("param"),
    ),
    check_fn=_available,
    emoji="📺",
)
