#!/usr/bin/env bash
# docker/ci-extra.sh -- jm's extras for the shared CI image (gh-2625).
#
# The image itself is canonical's (HAS_CI_IMAGE): docker/ci.Dockerfile is
# vendored, installs bootstrap.toml's CI_IMAGE_GROUPS through a pinned
# just-bashit release, then runs this file as root, once per base and arch.
# It holds only what no package list can say, and it is one of the image's
# SOURCES: editing it moves CI_IMAGE_SOURCE_HASH, so `make ci-image-check`
# (on lint) refuses the tree until a push of the branch rebuilds and repins
# the image.
#
#   ci-extra.sh                install the extras (the Dockerfile's RUN)
#   ci-extra.sh --fingerprint  print `tool<TAB>version` lines, hashed with
#                              dpkg's into CI_IMAGE_FINGERPRINT_<key>
#
# The one extra: CMake 3.16.3, the floor every generated project declares,
# for ci.yml's `Consumer smoke (CMake 3.16.3)` leg. No apt carries it, so the
# leg downloaded Kitware's release binary on every run; baked in here, it is
# fetched once per image build, checked against the sha256 Kitware publishes
# (cmake-3.16.3-SHA-256.txt), and the leg installs nothing live.
# scripts/consumer-smoke.sh uses /opt/cmake-<v> when CONSUMER_CMAKE_VERSION
# names <v>, and inside a CI image refuses to download one that is absent.
#
# Only on ubuntu 22.04 and amd64: that is the image the floor leg runs in,
# and Kitware ships no aarch64 binary of 3.16.
set -euo pipefail

FLOOR_CMAKE=3.16.3
FLOOR_CMAKE_SHA256=3e15dadfec8d54eda39c2f266fc1e571c1b88bf32f9d221c8a039b07234206fa
FLOOR_DIR=/opt/cmake-$FLOOR_CMAKE

# Every tool installed outside dpkg, so the weekly comparison can see it. An
# image without the floor prints nothing: its fingerprint stays dpkg's alone.
if [ "${1:-}" = --fingerprint ]; then
    if [ -x "$FLOOR_DIR/bin/cmake" ]; then
        printf 'cmake-floor\t%s\n' "$("$FLOOR_DIR/bin/cmake" --version | head -1)"
    fi
    exit 0
fi

. /etc/os-release
if [ "${VERSION_ID:-}" != 22.04 ] || [ "$(uname -m)" != x86_64 ]; then
    echo "ci-extra: no floor CMake on ${ID:-?} ${VERSION_ID:-?} $(uname -m)"
    exit 0
fi

tarball=cmake-$FLOOR_CMAKE-Linux-x86_64
# Downloaded to a file, checked, then unpacked -- never piped -- and curl
# --fail, so an error page is not saved as the archive.
curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 -o /tmp/cmake.tgz \
    "https://github.com/Kitware/CMake/releases/download/v$FLOOR_CMAKE/$tarball.tar.gz"
echo "$FLOOR_CMAKE_SHA256  /tmp/cmake.tgz" | sha256sum -c -
tar -xzf /tmp/cmake.tgz -C /opt
mv "/opt/$tarball" "$FLOOR_DIR"
rm -f /tmp/cmake.tgz

# The assertion: an image whose floor CMake does not run is never published.
got=$("$FLOOR_DIR/bin/cmake" --version | head -1)
[ "$got" = "cmake version $FLOOR_CMAKE" ] || {
    echo "ci-extra: $FLOOR_DIR/bin/cmake reports '$got'" >&2
    exit 1
}
echo "ci-extra: $got in $FLOOR_DIR"
