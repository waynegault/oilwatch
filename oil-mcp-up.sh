#!/bin/sh
# Start the OilWatch MCP server on the Windows host, on demand, and let it die.
#
#   oil-mcp-up [ttl_minutes]        # default 240; oil-mcp-down stops it sooner
#
# The gateway cannot start an HTTP MCP server, so a transport that is not a spawned
# stdio child needs something to start it: this is that something. Run it before the
# first oilwatch tool call of a session. It is idempotent (a server already listening
# is left alone) and it schedules its own stop, so nothing is left running.
#
# Why HTTP at all: a spawned stdio child that misses its init budget can take the whole
# gateway down with a child-cleanup rejection (an OpenClaw defect, not OilWatch's); a
# server reached over a URL cannot — only a spawned child has an authority to lose.
#
# Install it with install-wsl-helpers.sh from this checkout, never by hand: it must
# reach WSL as an LF-only file, and a CRLF shebang fails to exec with an error that
# reads like a missing file. The repo copy is the canonical one.
set -eu

REPO='/mnt/c/Users/wayne/GitHub/Python/Projects/Oil Price Webscraper'
PYTHON="$REPO/.venv/Scripts/python.exe"
PORT="${MCP_PORT:-8000}"
TTL_MINUTES="${1:-240}"

host=$(ip route show default | awk '{print $3; exit}')
if [ -z "$host" ]; then
  echo "oil-mcp-up: no default route, so the Windows host cannot be found" >&2
  exit 1
fi
if [ ! -x "$PYTHON" ]; then
  echo "oil-mcp-up: no interpreter at $PYTHON" >&2
  exit 1
fi

# A listening server answers 400 to a bare POST (no MCP content-type); curl reports
# that as success, which is exactly the question being asked here: is anything there?
probe() { curl -s -m 2 -o /dev/null "http://$host:$PORT/mcp"; }

if probe; then
  echo "oil-mcp-up: already listening on $host:$PORT"
  exit 0
fi

# Launched as a Windows process through interop: WSL translates the *executable* path
# but not argument paths, which is why nothing here hands it a /mnt/c path. WSLENV is
# what carries MCP_HOST across the boundary — without it the server binds loopback and
# WSL cannot reach it. It gets no console, so no window appears.
MCP_HOST=0.0.0.0 WSLENV=MCP_HOST nohup "$PYTHON" -m oilwatch.mcp_server >/dev/null 2>&1 &

i=0
while [ "$i" -lt 40 ]; do
  if probe; then
    # A bounded life, so a forgotten server cannot become the resident one this whole
    # arrangement exists to avoid. The sibling is found by this script's own directory:
    # ~/.local/bin is not on the PATH of a non-login WSL shell, and systemd-run would
    # then be handed an empty command. The unit is named so a new start replaces the
    # previous stop — two pending timers could kill a server a later call is using.
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    DOWN="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/oil-mcp-down"
    systemctl --user stop oil-mcp-ttl.timer >/dev/null 2>&1 || true
    systemctl --user reset-failed oil-mcp-ttl.service >/dev/null 2>&1 || true
    # The unit's working directory is set to a Windows-side one, because its default
    # is the user manager's (/home/wayne), which interop hands to a Windows process as
    # a \\wsl.localhost\... UNC path — and cmd.exe then prints "CMD.EXE was started
    # with the above path as the current directory. UNC paths are not supported" into
    # the unit's own journal, where it reads like a fault and is not one. Nothing else
    # about the run changes; the path is where `cd` lands, not where the script lives.
    if systemd-run --user --unit=oil-mcp-ttl --on-active="${TTL_MINUTES}min" \
      --working-directory=/mnt/c/Windows "$DOWN" >/dev/null 2>&1; then
      echo "oil-mcp-up: listening on $host:$PORT (stops itself in ${TTL_MINUTES} min; 'oil-mcp-down' stops it now)"
    else
      echo "oil-mcp-up: listening on $host:$PORT (could NOT schedule the stop - run oil-mcp-down when done)" >&2
    fi
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done

echo "oil-mcp-up: nothing answered on $host:$PORT after 40s" >&2
echo "oil-mcp-up: start it by hand and read the error:" >&2
echo "  cd \"$REPO\" && MCP_HOST=0.0.0.0 ./.venv/Scripts/python.exe -m oilwatch.mcp_server" >&2
exit 1
