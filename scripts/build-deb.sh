#!/usr/bin/env bash
# Build a .deb package using fpm.
# Requires: python3, pip3, fpm (gem install fpm)
set -euo pipefail

PKG_NAME="burnctl"
PKG_VERSION=$(python3 -c "from burnctl import __version__; print(__version__)" 2>/dev/null \
  || python3 -c "import importlib.metadata; print(importlib.metadata.version('burnctl'))")
ARCH="all"
STAGING="$(mktemp -d)"

trap 'rm -rf "${STAGING}"' EXIT

# Install into staging root
pip3 install --root "${STAGING}" --no-deps --no-warn-script-location .

# Build .deb
fpm \
  -s dir \
  -t deb \
  -n "${PKG_NAME}" \
  -v "${PKG_VERSION}" \
  -a "${ARCH}" \
  --description "Unified AI coding agent usage reporter" \
  --maintainer "Matthew Tennie <theaiorchard@gmail.com>" \
  --url "https://github.com/matttennie/burnctl" \
  --license "MIT" \
  --depends python3 \
  --deb-no-default-config-files \
  -C "${STAGING}" \
  .

echo "Built: ${PKG_NAME}_${PKG_VERSION}_${ARCH}.deb"
