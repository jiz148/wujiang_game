# Wujiang Game

## Architecture

Code is organised by domain so that strategic and tactical work stay independent:

- `src/wujiang/platform/` — HTTP kernel, accounts, analytics, match history, security. Knows nothing about gameplay.
- `src/wujiang/tactical/` — the grid battle: engine, heroes, rooms, battle endpoints.
- `src/wujiang/strategic/` — the campaign: world, cities, armies, diplomacy, relics, campaign endpoints.
- `src/wujiang/bridge/` — the only channel between the two game domains.
- `static/` mirrors the same split as ES modules, with `static/bridge/campaign-battle.js` as its seam.

The two game domains must not import each other. See [docs/架构分层说明.md](docs/架构分层说明.md) for the full layering rules, the bridge contracts and a path map from the older docs.

## Quick Start

- Local single-machine run:
  - `python run.py`
- Tests (run from the repository root; `replays/` resolves relative to the working directory):
  - `python -m unittest discover -s tests`
  - `node tools/verify_frontend_modules.mjs` link-checks the frontend module graph.
  - `python tools/verify_frontend_globals.py` scans for identifiers no module imports or declares.
  - `python tools/verify_frontend_dom_ids.py` finds `$("...")` lookups whose element no longer exists in the markup.
  - `python tools/verify_frontend_boot.py` drives a real browser through gate, menu, campaign and battle. Static checks cannot see a runtime DOM error, and a failed render reaches the player as a blank screen. Skips itself when no browser is installed.
- Windows online test by double-click:
  - Double-click [start_windows_server.bat](/C:/Users/jiz14/TeamGH/wujiang_game/start_windows_server.bat)
  - The launcher now defaults to a temporary public HTTPS tunnel, so friends outside your LAN can open the generated link directly.
- Windows LAN / manual public-address test:
  - `powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_server.ps1 -LanOnly`
  - Or run `python run.py --host 0.0.0.0 --port 8000 --public-base-url http://YOUR_IP:8000`
- Optional Windows Firewall helper:
  - Temporary while the server is running: built into [start_windows_server.bat](/C:/Users/jiz14/TeamGH/wujiang_game/start_windows_server.bat) for LAN/manual mode
  - Persistent manual rule: [open_firewall_8000.bat](/C:/Users/jiz14/TeamGH/wujiang_game/open_firewall_8000.bat) as Administrator
  - Temporary rule cleanup fallback: [close_temporary_firewall_8000.bat](/C:/Users/jiz14/TeamGH/wujiang_game/close_temporary_firewall_8000.bat) as Administrator

## Windows Helpers

- [scripts/start_windows_server.ps1](/C:/Users/jiz14/TeamGH/wujiang_game/scripts/start_windows_server.ps1)
  - Defaults to a temporary public Cloudflare Quick Tunnel for internet testing, automatically downloads `cloudflared` into `tools/cloudflared/` when needed, and opens the public homepage link in your browser.
  - You can force LAN/manual mode with `-LanOnly`.
- [scripts/open_firewall_port.ps1](/C:/Users/jiz14/TeamGH/wujiang_game/scripts/open_firewall_port.ps1)
  - Opens or removes an inbound TCP firewall rule for the selected port.

## Production Deployment

The production container, TLS proxy, persistent volumes, release audit, and rollback procedure are documented in [docs/正式部署与回滚手册.md](C:/Users/jiz14/TeamGH/wujiang_game/docs/正式部署与回滚手册.md). Production is intentionally single-replica while SQLite and in-memory room state remain authoritative.

For a local Docker deployment, run `docker compose -f compose.local.yml up -d --build`, then open `http://127.0.0.1:8000`. The local service binds only to the loopback interface and keeps game data in named Docker volumes. If port 8000 is occupied, set `WUJIANG_LOCAL_PORT` first. The public HTTPS deployment files remain available for future use.
