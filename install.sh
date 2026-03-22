#!/bin/sh
# lsf installer
#
# Usage:
#   ./install.sh          -- user install (recommended, no sudo needed)
#   sudo ./install.sh     -- system-wide install
#   ./install.sh --dev    -- editable install for development

set -e

if [ "$1" = "--dev" ]; then
    echo "Installing lsf in editable mode..."
    python3 -m pip install --editable . --force-reinstall --break-system-packages
    echo "Done. 'lsf' now reflects live changes to the source."
    exit 0
fi

if [ "$(id -u)" = "0" ]; then
    echo "Installing lsf system-wide..."
    python3 -m pip install --force-reinstall --break-system-packages .
else
    echo "Installing lsf for current user..."
    python3 -m pip install --user --force-reinstall --break-system-packages .
fi

echo ""
echo "Done. Run 'lsf' to get started."
echo ""

# Warn if the user bin directory isn't on PATH
USER_BIN="$(python3 -m site --user-base)/bin"
case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *)
        echo "Note: $USER_BIN is not on your PATH."
        echo "Add this to your shell config (~/.bashrc, ~/.zshrc, etc.):"
        echo "  export PATH=\"$USER_BIN:\$PATH\""
        echo ""
        ;;
esac
