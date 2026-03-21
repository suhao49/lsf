#!/bin/sh
# lsf installer
# Uses pip to install properly via pyproject.toml.
#
# Usage:
#   sudo ./install.sh            -- installs system-wide to /usr/local
#   PREFIX=~/.local ./install.sh -- installs to ~/.local (no sudo needed)
#   ./install.sh --dev           -- editable install into current Python env

set -e

# Editable / dev install
if [ "$1" = "--dev" ]; then
    echo "Installing lsf in editable mode..."
    pip install --editable . --force-reinstall
    echo "Done. 'lsf' now reflects live changes to the source."
    exit 0
fi

PREFIX="${PREFIX:-/usr/local}"

echo "Installing lsf to $PREFIX..."

pip install \
    --prefix="$PREFIX" \
    --no-build-isolation \
    --force-reinstall \
    .

echo ""
echo "Done. Run 'lsf' to get started."
echo ""
echo "If $PREFIX/bin is not on your PATH, add this to your shell config:"
echo "  export PATH=\"$PREFIX/bin:\$PATH\""
echo ""
echo "Config will be created at ~/.config/lsf/config.toml on first run."
echo "To customise it beforehand:"
echo "  mkdir -p ~/.config/lsf"
echo "  cp config/config.toml.example ~/.config/lsf/config.toml"
