# Build a standalone Windows distribution of lsf (no Python required on the
# target machine) and zip it for release.
#
#   .\packaging\build-windows.ps1
#
# Requires: python with the project deps + pyinstaller installed
#   pip install textual pyinstaller
#
# Output: dist\lsf\               (the runnable folder, dist\lsf\lsf.exe)
#         dist\lsf-<ver>-windows-x64.zip

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if (-not $version) { throw "could not read version from pyproject.toml" }

# --specpath build keeps PyInstaller's generated lsf.spec out of the repo
# root, where it would collide with the RPM spec of the same name.
pyinstaller --noconfirm --clean `
    --name lsf `
    --onedir --console `
    --paths . `
    --collect-all textual `
    --specpath build `
    packaging/lsf-launcher.py
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

Copy-Item LICENSE, README.md dist\lsf\

$zip = "dist\lsf-$version-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\lsf -DestinationPath $zip
Write-Host "Built $zip"
