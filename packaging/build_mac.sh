#!/bin/bash
# Build "opc2ixon.app" and a zip ready to send.
# Usage (from anywhere):   bash packaging/build_mac.sh
# Result:                  dist/opc2ixon-<version>-macOS-<arch>.zip
set -euo pipefail
cd "$(dirname "$0")/.."

# --- find a Python >= 3.10 (the one in /usr/bin is usually too old) ----------------
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)'; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3.10-3.14 not found. Install it from https://www.python.org/downloads/" >&2
    exit 1
fi
echo ">> Using $($PY --version) ($(command -v $PY))"

# --- clean build environment (separate from the development .venv) ------------------
rm -rf .venv-build build dist
"$PY" -m venv .venv-build
BIN=.venv-build/bin
"$BIN/python" -m pip install --quiet --upgrade pip
"$BIN/python" -m pip install --quiet -r requirements.txt -r packaging/requirements-build.txt

# --- third party licenses (shipped inside the app) ----------------------------------
"$BIN/pip-licenses" --with-license-file --no-license-path --format=plain-vertical \
    --ignore-packages pyinstaller pyinstaller-hooks-contrib pip-licenses prettytable wcwidth \
                      altgraph macholib packaging setuptools pip tomli \
    --output-file packaging/THIRD_PARTY_LICENSES.txt

# --- build ----------------------------------------------------------------------------
"$BIN/pyinstaller" --noconfirm --clean --log-level ERROR packaging/opc2ixon.spec

VERSION=$("$BIN/python" -c "import opc2ixon as m; print(m.__version__)")
ARCH=$(uname -m)
PKG="opc2ixon-${VERSION}-macOS-${ARCH}"
ZIP="$PKG.zip"
# zip content: opc2ixon.app + license notices next to it
rm -rf "dist/$PKG"
mkdir "dist/$PKG"
ditto "dist/opc2ixon.app" "dist/$PKG/opc2ixon.app"
cp packaging/NOTICE.txt packaging/THIRD_PARTY_LICENSES.txt "dist/$PKG/"
(cd dist && ditto -c -k --keepParent "$PKG" "$ZIP")

echo
echo ">> Done: dist/$ZIP"
echo "   Test it:  open \"dist/opc2ixon.app\""
