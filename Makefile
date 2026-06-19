# just-makeit — development control centre
#
# Targets:
#   make                   Run unit tests (default)
#   make test              Run unit test suite (pytest)
#   make test-fast         Run tests, stop on first failure
#   make test-examples     Run end-to-end example builds (requires cmake)
#   make bench             Run scaffold benchmarks (pytest-benchmark)
#   make bench-save        Save benchmark baseline (tagged with git describe)
#   make bench-compare     Compare against last saved baseline
#   make lint              Run pre-commit hooks on all files
#   make build             Build wheel into dist/
#   make docs              Build docs site into site/
#   make docs-serve        Build and serve docs with live reload
#   make install           Install package in editable mode
#   make clean             Remove build artifacts
#   make examples-clean    Remove build artifacts from all examples
#   make help              Show this message

SHELL      = /bin/sh
PYTHON     ?= $(shell uv run --no-project python -c "import sys; print(sys.executable)" 2>/dev/null || python3)
UV         = uv
PYTEST          = $(UV) run --no-project --with pytest --with numpy --with just-buildit pytest
PYTEST_B        = $(UV) run --no-project --with pytest --with pytest-benchmark --with numpy --with just-buildit pytest
PYTEST_EXAMPLES = $(UV) run --with pytest --with numpy pytest
ZENSICAL_RUN = $(UV) run --group dev
BENCH_TAG  ?= $(shell git describe --tags --dirty 2>/dev/null || date +%Y%m%d)

.PHONY: all test test-fast test-examples bench bench-save bench-compare lint build docs docs-serve install clean examples-clean help

all: test

# ── Test ─────────────────────────────────────────────────────────────────────

test:
	$(PYTEST) -v --ignore=tests/test_examples.py

test-fast:
	$(PYTEST) -x -q

test-examples:
	$(PYTEST_EXAMPLES) tests/test_examples.py -v

# ── Bench ─────────────────────────────────────────────────────────────────────

bench:
	$(PYTEST_B) tests/bench_scaffold.py -v --benchmark-disable-gc

bench-save:
	$(PYTEST_B) tests/bench_scaffold.py \
		--benchmark-save=$(BENCH_TAG) --benchmark-disable-gc

bench-compare:
	$(PYTEST_B) tests/bench_scaffold.py \
		--benchmark-compare --benchmark-disable-gc

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
	$(PYTHON) scripts/copy_examples.py
	$(ZENSICAL_RUN) zensical build --clean

docs-serve:
	$(PYTHON) scripts/copy_examples.py
	$(ZENSICAL_RUN) zensical serve

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
	@echo "  make test-examples run end-to-end example builds (requires cmake)"
	@echo "  make bench         run scaffold benchmarks"
	@echo "  make bench-save    save baseline (git describe tag)"
	@echo "  make bench-compare compare against last saved baseline"
	@echo "  make lint          run pre-commit hooks on all files"
	@echo "  make build         build wheel → dist/"
	@echo "  make docs          build docs → site/"
	@echo "  make docs-serve    build and serve with live reload"
	@echo "  make install       install dev dependencies (uv sync)"
	@echo "  make clean         remove build artifacts"
	@echo "  make examples-clean  remove build artifacts from all examples"
	@echo ""
