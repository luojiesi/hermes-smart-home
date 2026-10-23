# hermes-smart-home

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that adds five home-device tools:

| Tool | Device | Transport |
|---|---|---|
| `tesla` | Tesla vehicles | Tesla Fleet API (cloud) |
| `tapo` | TP-Link Tapo switches/plugs | LAN (python-kasa, KLAP) |
| `lgtv` | LG webOS TVs | LAN (PyWebOSTV), Wake-on-LAN |
| `sonos` | Sonos speakers | LAN (SoCo) |
| `rheem` | Rheem water heaters | Rheem EcoNet cloud (pyeconet) |

Each tool only appears to the model when its Python SDK is installed **and** its config/credentials exist,
so you can use any subset.

## Install

```bash
hermes plugins install https://github.com/luojiesi/hermes-smart-home
hermes plugins enable smart-home
```

## Configure

All state lives under `<HERMES_HOME>/smart_home/` (profile-aware via `get_hermes_home()`):

- `tesla.yaml`: `token_file`, `private_key_file`, `api_audience` (Fleet API region URL). You need your own
  Tesla developer app; `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET` (in Hermes secrets) are used to refresh the token.
- `tapo.yaml`: `known_ips`, `subnet`, `device_cache`. Secrets: `TAPO_EMAIL`, `TAPO_PASSWORD` (your TP-Link account,
  required by the local KLAP handshake).
- `lgtv.yaml`: `tvs` (name → ip/mac), `keys_file`. First connection shows a pairing prompt on the TV.
- `sonos.yaml`: `coordinator_ip`, `speakers`.
- Rheem: secrets `RHEEM_EMAIL`, `RHEEM_PASSWORD` (EcoNet account).

Secrets are read through Hermes's `agent.secret_scope.get_secret`, never from files.

## What this plugin does that you should know about

- **Network calls to third parties:** Tesla (`auth.tesla.com`, `fleet-api.*.tesla.com`) and Rheem EcoNet cloud.
  Everything else talks only to devices on your LAN.
- **LAN scanning:** if a Tapo plug or LG TV stops answering at its known IP, the tool scans your /24 (Tapo
  discovery broadcast; TCP 3001 probe for webOS) to find its new DHCP address.
- **Stored credentials it writes:** the Tesla OAuth token file (refreshed with your own Tesla app's client
  credentials), the LG TV pairing keys, and the Tapo/LG device-IP caches, all inside `<HERMES_HOME>/smart_home/`.
- **Physical actions:** these tools can unlock a car, open a trunk or garage door, and switch mains-powered
  devices. Pair with Hermes approvals/guard plugins if others can talk to your agent.
- No telemetry, no background processes, no self-update.

## Tests

```bash
python -m pytest tests -q
```

## License

MIT
