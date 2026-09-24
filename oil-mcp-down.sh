#!/bin/sh
# Stop the OilWatch MCP server that oil-mcp-up started. Harmless when nothing is up.
#
# Install it with install-wsl-helpers.sh from this checkout, never by hand: it must
# reach WSL as an LF-only file, and a CRLF shebang fails to exec with an error that
# reads like a missing file. The repo copy is the canonical one.
#
# The server is a *Windows* process. This machine sets appendWindowsPath = false in
# /etc/wsl.conf (deliberate, see the host notes), so no Windows binary is ever on the
# PATH — each one is named by absolute path. netstat and taskkill are .exe files that
# interop runs directly, so cmd.exe is not needed at all (the pipe below is this
# shell's, not cmd's).
# tr -d '\r' matters: Windows tool output ends every field in a carriage return, and a
# pid with a \r inside it is not a pid taskkill accepts.
set -eu

SYS=/mnt/c/Windows/System32
PORT="${MCP_PORT:-8000}"

for exe in "$SYS/netstat.exe" "$SYS/taskkill.exe"; do
  if [ ! -x "$exe" ]; then
    echo "oil-mcp-down: no $exe on this machine" >&2
    exit 1
  fi
done

pids=$("$SYS/netstat.exe" -ano | tr -d '\r' \
  | awk -v port=":$PORT" '$1 == "TCP" && $4 == "LISTENING" && $2 ~ port { print $5 }' \
  | sort -u)

if [ -z "$pids" ]; then
  echo "oil-mcp-down: nothing listening on port $PORT"
  exit 0
fi

stopped=0
for pid in $pids; do
  [ "$pid" = "0" ] && continue
  if "$SYS/taskkill.exe" /PID "$pid" /F >/dev/null 2>&1; then
    echo "oil-mcp-down: stopped pid $pid"
    stopped=$((stopped + 1))
  else
    echo "oil-mcp-down: could not stop pid $pid" >&2
  fi
done

[ "$stopped" -gt 0 ] || exit 1
