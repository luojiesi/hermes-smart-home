"""
Rheem EcoNet water heater control tool for Hermes.

Controls Rheem heat pump water heater via the EcoNet cloud API (pyeconet).
Requires RHEEM_EMAIL and RHEEM_PASSWORD.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


def _check_rheem_reqs() -> bool:
    email, password = _get_credentials()
    return bool(email and password)


def _get_credentials():
    """Rheem EcoNet credentials via Hermes's profile-aware secret store (catalog rule 11)."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:  # older Hermes
        get_secret = lambda k, d=None: os.getenv(k, d)  # noqa: E731
    try:
        return get_secret("RHEEM_EMAIL") or "", get_secret("RHEEM_PASSWORD") or ""
    except Exception:  # e.g. UnscopedSecretError under multiplexing: fail closed
        return "", ""


MODE_MAP = {
    "off": 1,
    "electric": 2,
    "energy_saving": 3,
    "heat_pump": 4,
    "high_demand": 5,
    "vacation": 9,
}

MODE_NAMES = {v: k for k, v in MODE_MAP.items()}


async def _do_action(action: str, param: str = None):
    from pyeconet import EcoNetApiInterface
    from pyeconet.equipment import EquipmentType
    from pyeconet.equipment.water_heater import WaterHeaterOperationMode

    email, password = _get_credentials()
    api = await EcoNetApiInterface.login(email=email, password=password)
    result = await api.get_equipment_by_type([EquipmentType.WATER_HEATER])
    heaters = result.get(EquipmentType.WATER_HEATER, [])

    if not heaters:
        return {"error": "No water heater found on EcoNet account"}

    wh = heaters[0]

    if action == "status":
        return {
            "success": True,
            "name": wh.device_name,
            "connected": wh.connected,
            "running": wh.running,
            "running_state": wh.running_state,
            "set_point_f": wh.set_point,
            "set_point_range_f": list(wh.set_point_limits),
            "mode": MODE_NAMES.get(wh.mode, str(wh.mode)),
            "available_modes": [MODE_NAMES.get(m.value, m.name) for m in wh.modes],
            "hot_water_availability": f"{wh.tank_hot_water_availability}%",
            "vacation": wh.vacation,
            "compressor_health": f"{wh.compressor_health}%",
            "tank_health": f"{wh.tank_health}%",
            "leak_installed": wh.leak_installed,
            "shutoff_valve_open": wh.shutoff_valve_open,
            "alert_count": wh.alert_count,
        }

    elif action == "set_temp":
        if not param:
            return {"error": "param (temperature in °F) is required"}
        temp = int(param)
        low, high = wh.set_point_limits
        if temp < low or temp > high:
            return {"error": f"Temperature must be between {low}°F and {high}°F"}
        wh.set_set_point(temp)
        await api.publish(wh)
        return {"success": True, "message": f"Temperature set to {temp}°F"}

    elif action == "set_mode":
        if not param:
            return {
                "error": "param required: off, electric, energy_saving, heat_pump, high_demand, vacation",
                "available_modes": [MODE_NAMES.get(m.value, m.name) for m in wh.modes],
            }
        mode_val = MODE_MAP.get(param.lower().strip())
        if mode_val is None:
            return {"error": f"Unknown mode '{param}'. Use: off, electric, energy_saving, heat_pump, high_demand, vacation"}
        # Find the matching WaterHeaterOperationMode enum
        target_mode = None
        for m in wh.modes:
            if m.value == mode_val:
                target_mode = m
                break
        if target_mode is None:
            return {"error": f"Mode '{param}' not supported by this water heater"}
        wh.set_mode(target_mode)
        await api.publish(wh)
        return {"success": True, "message": f"Mode set to {param}"}

    elif action == "set_vacation":
        if not param:
            return {"error": "param required: on or off"}
        on = param.lower().strip() in ("on", "true", "1", "yes")
        if on:
            wh.set_mode(WaterHeaterOperationMode.VACATION)
        else:
            # Return to energy saving mode when turning off vacation
            wh.set_mode(WaterHeaterOperationMode.ENERGY_SAVING)
        await api.publish(wh)
        return {"success": True, "message": f"Vacation mode {'enabled' if on else 'disabled'}"}

    else:
        return {"error": f"Unknown action: {action}"}


def rheem_control(action: str, param: str = None) -> str:
    try:
        result = asyncio.run(_do_action(action, param))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("Rheem tool error: %s", e, exc_info=True)
        return json.dumps({"error": str(e)})


RHEEM_SCHEMA = {
    "name": "rheem",
    "description": (
        "Control a Rheem heat pump water heater via EcoNet cloud API. "
        "Actions: status (temperature, mode, hot water availability, health), "
        "set_temp (param=°F, range 110-140), "
        "set_mode (param=off|electric|energy_saving|heat_pump|high_demand|vacation), "
        "set_vacation (param=on|off)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "set_temp", "set_mode", "set_vacation"],
                "description": "Action to perform",
            },
            "param": {
                "type": "string",
                "description": "Temperature in °F, mode name, or on/off for vacation",
            },
        },
        "required": ["action"],
    },
}


def _deps_present() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ['pyeconet'])


def _available() -> bool:
    """Expose the tool only when its SDK is importable AND its config/credentials exist."""
    return _deps_present() and _check_rheem_reqs()


TOOL_SPEC = dict(
    name="rheem",
    toolset="rheem",
    schema=RHEEM_SCHEMA,
    handler=lambda args, **kw: rheem_control(
        action=args["action"],
        param=args.get("param"),
    ),
    check_fn=_available,
    requires_env=["RHEEM_EMAIL", "RHEEM_PASSWORD"],
    emoji="🔥",
)
