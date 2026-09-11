@echo off
rem Start the OilWatch MCP server bound to all interfaces, so an agent running
rem in WSL (OpenClaw/Hal) can reach it. The address WSL must use depends on the
rem WSL networking mode: under NAT it is the gateway from `wsl -e ip route`
rem (currently http://172.28.144.1:8000/mcp); under mirrored it is the Windows
rem LAN IP. OpenClaw's config points at whichever is current.
cd /d "%~dp0"
set MCP_HOST=0.0.0.0
set MCP_PORT=8000
.venv\Scripts\python -m oilwatch.mcp_server
