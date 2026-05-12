#!/usr/bin/env sh
set -e
uv run --no-project --with pytest --with numpy --with just-buildit pytest -v
