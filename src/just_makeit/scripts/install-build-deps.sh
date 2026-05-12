#!/usr/bin/env sh
set -e
sudo apt-get install -y cmake gcc 2>/dev/null || brew install cmake 2>/dev/null || true
