@echo off
rem Serve the OilWatch MCP tools over streamable HTTP on all interfaces, for a
rem client that dials a URL (or to poke at them by hand). OpenClaw itself no
rem longer uses this - it spawns `.venv\Scripts\python -m oilwatch.mcp_server
rem --stdio` on demand, which needs no socket. A WSL client reaching this HTTP
rem endpoint must use the Windows host address (under NAT, the gateway from
rem `wsl -e ip route`), never localhost.
cd /d "%~dp0"
set MCP_HOST=0.0.0.0
set MCP_PORT=8000
.venv\Scripts\python -m oilwatch.mcp_server
