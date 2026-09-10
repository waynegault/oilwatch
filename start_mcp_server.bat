@echo off
rem Start the OilWatch MCP server bound to all interfaces, so an agent running
rem in WSL (OpenClaw/Hal) can reach it over the mirrored Windows LAN IP.
rem OpenClaw config points at http://<windows-lan-ip>:8000/mcp
rem Find the current IP with: wsl -e ip -4 -o addr show   (the eth1 address)
cd /d "%~dp0"
set MCP_HOST=0.0.0.0
set MCP_PORT=8000
.venv\Scripts\python -m oilwatch.mcp_server
