#!/usr/bin/env bash
# Build a standalone Linux/macOS distribution of lsf (no Python required on
# the target machine) and tar it for release.
#
#   bash packaging/build-posix.sh
#
# Requires: python with the project deps + pyinstaller installed
#   pip install textual pyinstaller
#
# Output: dist/lsf/                          (the runnable folder, dist/lsf/lsf)
#         dist/lsf-<ver>-<os>-<arch>.tar.gz

set -euo pipefail
cd "$(dirname "$0")/.."

version=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")

case "$(uname -s)" in
    Darwin) os=macos ;;
    *)      os=linux ;;
esac
case "$(uname -m)" in
    arm64|aarch64) arch=arm64 ;;
    *)             arch=x64   ;;
esac

# --specpath build keeps PyInstaller's generated lsf.spec out of the repo
# root, where it would collide with the RPM spec of the same name.
pyinstaller --noconfirm --clean \
    --name lsf \
    --onedir --console \
    --paths . \
    --collect-all textual \
    --specpath build \
    packaging/lsf-launcher.py

cp LICENSE README.md dist/lsf/

tarball="dist/lsf-$version-$os-$arch.tar.gz"
tar -C dist -czf "$tarball" lsf
echo "Built $tarball"
