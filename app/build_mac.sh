#!/bin/bash
# Builds dist/N64RA-Bridge-mac.zip: "N64RA Bridge.app" (Intel + Apple silicon)
# with its own Python (python-build-standalone) and pyserial.
# Needs bash, curl, tar, unzip, pip (to fetch pyserial), zig and lipo
# (llvm-lipo) for the launcher, and codesign (on a Mac) or rcodesign
# (cargo install apple-codesign) to sign it: macOS only asks for Local
# Network permission for a signed app.
set -e
cd "$(dirname "$0")"
PBS_TAG=20261021
PY_VER=3.12.15
PYSERIAL=3.5
BASE="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG"
CACHE=.cache; mkdir -p "$CACHE"
rm -rf dist build && mkdir -p dist build

fetch() { # target triple -> cached tarball path
  local f="cpython-$PY_VER+$PBS_TAG-$1-install_only_stripped.tar.gz"
  [ -s "$CACHE/$f" ] || curl -fL --retry 3 -o "$CACHE/$f" "$BASE/${f//+/%2B}"
  echo "$CACHE/$f"
}
prune() { # python root, stdlib dir: drop what the window never uses
  rm -rf "$2"/test "$2"/idlelib "$2"/ensurepip "$2"/turtledemo "$2"/lib2to3 "$2"/tkinter/test \
         "$2"/site-packages/pip* "$1"/include "$1"/share
  rm -f "$1"/bin/pip* "$1"/bin/idle* "$1"/bin/pydoc* "$1"/bin/2to3*
  find "$1" -name __pycache__ -type d -prune -exec rm -rf {} +
}
W="$CACHE/pyserial-$PYSERIAL-py2.py3-none-any.whl"
[ -s "$W" ] || pip download -q "pyserial==$PYSERIAL" --no-deps -d "$CACHE"

# launcher: one universal binary
ZIG=""; command -v zig >/dev/null && ZIG=zig
[ -z "$ZIG" ] && python3 -m ziglang version >/dev/null 2>&1 && ZIG="python3 -m ziglang"
LIPO=$(command -v lipo || command -v llvm-lipo || ls /usr/bin/llvm-lipo-* 2>/dev/null | head -1)
$ZIG cc -target aarch64-macos.11.0 -O2 -Wl,-headerpad_max_install_names -o build/l-arm64 macos/launcher.c
$ZIG cc -target x86_64-macos.10.15 -O2 -Wl,-headerpad_max_install_names -o build/l-x86_64 macos/launcher.c
"$LIPO" -create build/l-arm64 build/l-x86_64 -output build/launcher

APP="build/N64RA Bridge.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
cp macos/Info.plist "$APP/Contents/"
cp macos/AppIcon.icns "$APP/Contents/Resources/"
cp build/launcher "$APP/Contents/MacOS/N64RA Bridge"
chmod +x "$APP/Contents/MacOS/N64RA Bridge"
for arch in x86_64 aarch64; do
  P="$APP/Contents/Resources/python-$arch"
  mkdir -p "$P"
  tar xzf "$(fetch $arch-apple-darwin)" -C "$P" --strip-components=1
  prune "$P" "$P/lib/python3.12"
  (cd "$P/lib/python3.12/site-packages" && unzip -qo "$OLDPWD/$W" -x '*.dist-info/*')
done
cp n64ra_gui.pyw icon.png "$APP/Contents/Resources/app/"
cp ../bridge/n64ra_bridge.py "$APP/Contents/Resources/app/"
cp "macos/READ ME.txt" build/
find build -name __pycache__ -type d -prune -exec rm -rf {} +

if command -v codesign >/dev/null; then
  codesign --force --deep --sign - "$APP"
elif command -v rcodesign >/dev/null; then
  rcodesign sign "$APP" >build/sign.log 2>&1 || { tail -5 build/sign.log; exit 1; }
else
  echo "warning: not signed (no codesign or rcodesign): Local Network permission will not work"
fi
(cd build && zip -qry ../dist/N64RA-Bridge-mac.zip "N64RA Bridge.app" "READ ME.txt" -x '*.DS_Store')
rm -rf build
ls -l dist
