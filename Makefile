# just-makeit — development control centre
#
# Targets:
#   make               Run tests (default)
#   make test          Run full test suite (pytest)
#   make test-fast     Run tests, stop on first failure
#   make lint          Run pre-commit hooks on all files
#   make build         Build wheel into dist/
#   make docs          Build docs site into site/
#   make docs-serve    Build and serve docs with live reload
#   make install       Install package in editable mode
#   make clean         Remove build artifacts
#   make examples-clean  Remove build artifacts from all examples
#   make help          Show this message

SHELL   = /bin/sh
PYTHON  ?= $(shell uv run --no-project python -c "import sys; print(sys.executable)" 2>/dev/null || python3)
UV      = uv
PYTEST  = $(UV) run --no-project --with pytest --with numpy --with just-buildit pytest
ZENSICAL = $(UV) run --group dev zensical

.PHONY: all test test-fast lint build docs docs-serve install clean examples-clean help

all: test

# ── Test ─────────────────────────────────────────────────────────────────────

test:
	$(PYTEST) -v

test-fast:
	$(PYTEST) -x -q

# ── Lint ──────────────────────────────────────────────────────────────────────

lint:
	$(UV) run --group dev pre-commit run --all-files

# ── Build ─────────────────────────────────────────────────────────────────────

build:
	PYTHONPATH=src $(UV) build --wheel --no-build-isolation
	@echo ""
	@ls -lh dist/*.whl

# ── Docs ──────────────────────────────────────────────────────────────────────

docs:
	$(UV) run --no-project --with "zensical>=0.0.29" zensical build --clean

docs-serve:
	$(UV) run --no-project --with "zensical>=0.0.29" zensical serve

# ── Dev install ───────────────────────────────────────────────────────────────

install:
	$(UV) sync --group dev

# ── Clean ─────────────────────────────────────────────────────────────────────

clean:
	rm -rf dist/ site/ .pytest_cache/
	find src -name "*.pyc" -delete
	find src -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true

examples-clean:
	@for d in examples/*/; do \
	    [ -f "$$d/Makefile" ] && $(MAKE) -C "$$d" clean 2>/dev/null || true; \
	done
	find examples -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  just-makeit development"
	@echo ""
	@echo "  make               run tests"
	@echo "  make test          run full test suite"
	@echo "  make test-fast     stop on first failure"
	@echo "  make lint          run pre-commit hooks on all files"
	@echo "  make build         build wheel → dist/"
	@echo "  make docs          build docs → site/"
	@echo "  make docs-serve    build and serve with live reload"
	@echo "  make install       install dev dependencies (uv sync)"
	@echo "  make clean         remove build artifacts"
	@echo "  make examples-clean  remove build artifacts from all examples"
	@echo ""
