#!/bin/sh
# Install the OilWatch MCP helper launchers from this checkout into ~/.local/bin.
#
#   (from WSL, anywhere)  "/mnt/c/.../Oil Price Webscraper/install-wsl-helpers.sh"
#
# Run it from *inside WSL*, not from Windows: it installs WSL-side files, and its own
# path is how it finds the checkout.
#
# It exists so the launchers are version-controlled and installed by one route rather
# than hand-edited. The one thing that matters is line endings: each file must reach
# WSL as LF-only, because a CRLF shebang fails to exec with an error that reads like a
# missing file — which is exactly how the boundary bites, since editing these on the
# Windows side is the obvious thing to do. Re-running this reports drift instead of
# silently reinstalling.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest="${HOME}/.local/bin"
written=0

mkdir -p "$dest"

install_one() {
  src="$here/$1"
  target="$dest/$2"

  if [ ! -f "$src" ]; then
    echo "install-wsl-helpers: no $src" >&2
    exit 1
  fi

  # tr, not cp: the worktree copy is pinned to LF by .gitattributes, but a checkout
  # that somehow carries CRLF must not be able to install a shebang that cannot exec.
  if [ -f "$target" ] && tr -d '\r' < "$src" | cmp -s - "$target"; then
    echo "install-wsl-helpers: $2 is up to date"
    return 0
  fi

  tr -d '\r' < "$src" > "$target"
  chmod 755 "$target"
  sh -n "$target"
  echo "install-wsl-helpers: installed $2"
  written=$((written + 1))
}

install_one oil-mcp-up.sh oil-mcp-up
install_one oil-mcp-down.sh oil-mcp-down

echo "install-wsl-helpers: $written file(s) written to $dest"
echo

if command -v oil-mcp-up >/dev/null 2>&1; then
  echo "Before the first oilwatch tool call of a session:  oil-mcp-up [minutes]"
else
  echo "$dest is not always on the PATH, so start it by full path:"
  echo "  $dest/oil-mcp-up [minutes]      # and $dest/oil-mcp-down when done"
fi
