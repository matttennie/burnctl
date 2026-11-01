#!/bin/bash
# Local development build only. Existing builds are preserved, never deleted.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${BURNCTL_BUILD_PYTHON:-python3.14}"
VENV="$ROOT/.venv-macos"
BUILD_ROOT="$ROOT/build/macos"
mkdir -p "$BUILD_ROOT" "$ROOT/dist"
STAGE="$(mktemp -d "$BUILD_ROOT/build.XXXXXX")"
APP="$STAGE/Burnctl.app"

if [ ! -x "$VENV/bin/python" ]; then
    "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -r macos/build-requirements.txt

export MACOSX_DEPLOYMENT_TARGET=15.0
# Keep compiler and packaging caches inside this worktree.
export CLANG_MODULE_CACHE_PATH="$BUILD_ROOT/clang-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="$BUILD_ROOT/swift-cache"
export PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-cache"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers" "$APP/Contents/Resources"

swift build --package-path macos --configuration release --disable-sandbox
BIN="$(swift build --package-path macos --configuration release --show-bin-path --disable-sandbox)"
cp "$BIN/Burnctl" "$APP/Contents/MacOS/Burnctl"
cp macos/Info.plist "$APP/Contents/Info.plist"
cp LICENSE "$APP/Contents/Resources/LICENSE.txt"
# The bundled runtime may require a newer OS than the Swift interface.
MIN_OS="$("$VENV/bin/python" -c 'import sysconfig; target = tuple(map(int, (sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET") or "15.0").split("."))); print(".".join(map(str, max((15, 0), (target + (0,))[:2]))))')"
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $MIN_OS" "$APP/Contents/Info.plist"

"$VENV/bin/python" -m PyInstaller --onedir --name burnctl-engine \
    --paths "$ROOT" --distpath "$STAGE/engine" --workpath "$STAGE/pyinstaller" \
    --specpath "$STAGE" --collect-data certifi --noconfirm macos/engine_entry.py
cp -R "$STAGE/engine/burnctl-engine" "$APP/Contents/Resources/engine"
ln -s ../Resources/engine/burnctl-engine "$APP/Contents/Helpers/burnctl-engine"
"$VENV/bin/python" macos/collect-licenses.py "$APP/Contents/Resources/ThirdPartyNotices" \
    "$STAGE/pyinstaller/burnctl-engine/Analysis-00.toc"

swift macos/make-icon.swift "$STAGE/Burnctl.iconset"
iconutil -c icns "$STAGE/Burnctl.iconset" -o "$APP/Contents/Resources/Burnctl.icns"

# Ad-hoc signing is for local testing. No Developer ID, upload, or notarization.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
plutil -lint "$APP/Contents/Info.plist"

if [ -e "$ROOT/dist/Burnctl.app" ]; then
    mv "$ROOT/dist/Burnctl.app" "$STAGE/previous-Burnctl.app"
fi
mv "$APP" "$ROOT/dist/Burnctl.app"
printf '\nBuilt: %s\nOpen:  open "%s"\n' "$ROOT/dist/Burnctl.app" "$ROOT/dist/Burnctl.app"
