# Wujiang Game

## Quick Start

- Local single-machine run:
  - `python run.py`
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
