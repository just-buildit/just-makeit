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
#   make lint              Run pre-commit hooks on all files (the CI gate)
#   make format            Auto-fix formatting (ruff + mdformat)
#   make build             Build wheel into dist/
#   make docs              Build docs site into site/
#   make docs-serve        Build and serve docs with live reload
#   make docs-check        Pre-push docs gate: strict build + docs tests
#   make install           Install package in editable mode
#   make setup             One-time per clone: uv sync + pre-commit install
#   make bump-version VERSION=  Update version in pyproject.toml
#   make check-version VERSION= Verify version matches
#   make release-branch VERSION= Create release branch + bump
#   make tag-release VERSION=   Tag merged main + push
#   make release-watch VERSION= Watch release.yml + verify artifacts
#   make ship VERSION=          tag-release then release-watch
#   make clean             Remove build artifacts
#   make examples-clean    Remove build artifacts from all examples
#   make help              Show this message

SHELL      = /bin/sh
PYTHON     ?= $(shell uv run --no-project python -c "import sys; print(sys.executable)" 2>/dev/null || python3)
UV         = uv
BENCH_TAG  ?= $(shell git describe --tags --dirty 2>/dev/null || date +%Y%m%d)

# ── Tooling ───────────────────────────────────────────────────────────────────
# This block is the ONLY place a tool binary is named or given flags. Humans,
# the pre-commit hooks, and CI all reach the tools through the targets below,
# so changing a flag (or a tool) is a one-line edit here rather than a hunt
# through the Makefile, .pre-commit-config.yaml, and the workflow files.
# Versions live in pyproject.toml's `dev` group and are locked by uv.lock.
#
# Corollary: do NOT invoke a linter with `uvx` (or a global install). `uvx ruff`
# resolves to whatever released today, which formats differently from the
# pinned ruff and silently rewrites unrelated files. Use `make format`.
DEV_RUN    = $(UV) run --group dev
RUFF       = $(DEV_RUN) ruff
MDFORMAT   = $(DEV_RUN) mdformat
ZENSICAL   = $(DEV_RUN) zensical
PRE_COMMIT = $(DEV_RUN) pre-commit

# Each formatter runs over the whole tree so `make format` and the pre-commit
# hook can never disagree about scope. ruff reads its own excludes from
# pyproject.toml; mdformat has no config file here, so its exclusions are named
# once, below, instead of being duplicated into the hook config.
#
# examples/ and src/just_makeit/examples/ are GENERATED (assembled from
# .steps/), and templates/ holds <<placeholder>> markdown that is not valid
# until rendered — mdformat escapes the `<`, which then renders a stray
# backslash and breaks mkdocstrings/zensical. docs/index.md is the zensical
# landing page and is hand-laid-out.
RUFF_PATHS = .
# mdformat's own --exclude needs Python >=3.13 (it uses glob.translate), so the
# file list is built here instead: every tracked .md minus these prefixes. That
# also keeps the exclusions greppable in one place rather than as a second copy
# of this regex in .pre-commit-config.yaml.
MD_EXCLUDE_RE = ^(examples/|src/just_makeit/examples/|src/just_makeit/templates/|docs/index\.md$$)

# just-makeit's OWN runtime dependencies, mirrored from pyproject.toml's
# `[project] dependencies`. `--no-project` excludes the project *and its
# dependencies*, but the suite imports just_makeit from `src/` — so without
# these it runs the code under test with its dependencies missing. tomlkit's
# absence is silent (`_write_doc` falls back to `_dump`, and ~8 round-trip tests
# fail with no hint why); tomli's is fatal on 3.9/3.10, where `C.tomllib` is the
# backport. pyproject stays the source of truth — tests/test_test_env.py fails
# if this list drifts from it, so the duplication cannot rot.
JM_RUNTIME_DEPS = --with "tomlkit>=0.15.0" \
                  --with "tomli>=2.0.0; python_version < '3.11'"

# pytest runs three ways. The deltas are spelled out here rather than in three
# parallel command strings that drift independently:
#   PYTEST          unit suite — `--no-project` keeps the project env OUT, so
#                   the suite exercises the installed-package path; the
#                   generated projects it scaffolds build with just-buildit.
#   PYTEST_B        the same, plus pytest-benchmark.
#   PYTEST_EXAMPLES example builds — deliberately WITHOUT `--no-project`, since
#                   these need `just-makeit` itself importable from the project
#                   env (just-buildit arrives transitively as its build dep), so
#                   its runtime deps arrive with it.
PYTEST_DEPS     = --with pytest --with numpy
PYTEST_ISOLATED = $(UV) run --no-project $(PYTEST_DEPS) $(JM_RUNTIME_DEPS) \
                  --with just-buildit
PYTEST          = $(PYTEST_ISOLATED) pytest
PYTEST_B        = $(PYTEST_ISOLATED) --with pytest-benchmark pytest
PYTEST_EXAMPLES = $(UV) run $(PYTEST_DEPS) pytest

.PHONY: all test test-fast test-examples bench bench-save bench-compare \
        lint format lint-ruff lint-ruff-format lint-mdformat \
        build docs docs-serve docs-check install setup \
        bump-version check-version release-branch tag-release \
        release-watch ship clean examples-clean help

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

# `lint` is the gate (CI runs exactly this); `format` is the fixer you run
# locally. Both go through the tool variables above, so neither can drift from
# what the pre-commit hooks do — the hooks call these same targets.
# The hook install is a convenience, not the gate — so it must never be able to
# fail the gate. `git rev-parse --git-path` resolves the real hooks dir (in a
# worktree `.git` is a FILE, so the old `test -f .git/hooks/...` guard always
# missed and tried to reinstall), and a `core.hooksPath` that makes pre-commit
# refuse to install is reported rather than fatal.
lint:
	@hook=$$(git rev-parse --git-path hooks/pre-commit 2>/dev/null); \
	 if [ -n "$$hook" ] && [ ! -f "$$hook" ]; then \
	     $(PRE_COMMIT) install >/dev/null 2>&1 \
	         || echo "note: git hook not installed (continuing with lint)"; \
	 fi
	$(PRE_COMMIT) run --all-files

format:
	$(RUFF) format $(RUFF_PATHS)
	$(RUFF) check --fix --unsafe-fixes $(RUFF_PATHS)
	@$(MAKE) -s lint-mdformat

# Individual tool targets. These exist so .pre-commit-config.yaml can invoke a
# Makefile target instead of repeating the command line — the hooks decide WHEN
# to run (which paths trigger them); these decide HOW.
lint-ruff:
	$(RUFF) check --fix --unsafe-fixes $(RUFF_PATHS)

lint-ruff-format:
	$(RUFF) format $(RUFF_PATHS)

# mdformat needs Python >=3.10 (see pyproject's dev group). On a 3.9 dev env it
# is simply absent, so skip with a notice rather than failing — the CI lint job
# runs a modern Python and enforces it there. Same self-skip pattern as the
# mypy-backed stub-conformance gate.
lint-mdformat:
	@if $(MDFORMAT) --version >/dev/null 2>&1; then \
	    git ls-files '*.md' \
	        | grep -Ev '$(MD_EXCLUDE_RE)' \
	        | xargs -r $(MDFORMAT); \
	else \
	    echo "mdformat unavailable (needs Python >=3.10) — skipping"; \
	fi

# ── Build ─────────────────────────────────────────────────────────────────────

build:
	PYTHONPATH=src $(UV) build --wheel --no-build-isolation
	@echo ""
	@ls -lh dist/*.whl

# ── Docs ──────────────────────────────────────────────────────────────────────

docs:
	$(PYTHON) scripts/copy_examples.py
	$(ZENSICAL) build --clean --strict

docs-serve:
	$(PYTHON) scripts/copy_examples.py
	$(ZENSICAL) serve

# Pre-push docs gate: the strict build catches broken TOC anchors (which the
# test suite does NOT — only the CI docs build does), and the docs test catches
# mangled MkDocs tab blocks + other invariants. Run this before pushing docs.
docs-check:
	@echo "Docs gate: strict build (broken anchors) + docs invariants..."
	$(PYTHON) scripts/copy_examples.py
	$(ZENSICAL) build --strict
	$(PYTEST) tests/test_docs.py

# ── Dev install ───────────────────────────────────────────────────────────────

install:
	$(UV) sync --group dev

setup:
	$(UV) sync --group dev
	$(PRE_COMMIT) install

# ── Release ───────────────────────────────────────────────────────────────────

bump-version:
ifndef VERSION
	@echo "usage: make bump-version VERSION=<x.y.z>"
	@exit 1
endif
	sed -i 's/^version = "[^"]*"/version = "$(VERSION)"/' pyproject.toml
	@echo "Bumped to $(VERSION) in pyproject.toml"
	@echo "Next: edit CHANGELOG.md, commit, push PR, merge, then:"
	@echo "      git checkout main && git pull && make tag-release VERSION=$(VERSION)"

check-version:
ifndef VERSION
	@echo "usage: make check-version VERSION=<x.y.z>"
	@exit 1
endif
	@PY=$$(grep '^version = ' pyproject.toml | sed 's/.*"\(.*\)".*/\1/'); \
	 if [ "$$PY" != "$(VERSION)" ]; then \
	     echo "ERROR: pyproject.toml has $$PY, expected $(VERSION)"; exit 1; \
	 fi; \
	 echo "Version OK: $(VERSION)"

release-branch:
ifndef VERSION
	@echo "usage: make release-branch VERSION=<x.y.z>"
	@exit 1
endif
	git checkout -b chore/release-$(VERSION) origin/main
	$(MAKE) bump-version VERSION=$(VERSION)
	@echo "  - edit CHANGELOG.md ([Unreleased] -> [$(VERSION)] -- YYYY-MM-DD)"
	@echo "  - git commit -am 'chore: release v$(VERSION)', push PR, merge"
	@echo "  - then: git checkout main && git pull && make tag-release"

tag-release:
ifndef VERSION
	@echo "usage: make tag-release VERSION=<x.y.z>"
	@exit 1
endif
	@git fetch origin main
	@CURRENT=$$(git rev-parse HEAD); \
	 ORIGIN=$$(git rev-parse origin/main); \
	 if [ "$$CURRENT" != "$$ORIGIN" ]; then \
	     echo "ERROR: not at origin/main — checkout main and pull first"; \
	     exit 1; \
	 fi
	$(MAKE) check-version VERSION=$(VERSION)
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	git push origin "v$(VERSION)"
	@echo "Tagged v$(VERSION) — release workflow starting on GitHub"
	@echo "Watch + verify it with: make release-watch VERSION=$(VERSION)"

# Autonomously watch release.yml for v$(VERSION): stream job outcomes, auto-rerun
# ONE pre-publish flake (safe — publish is gated behind smoke), and verify the
# real artifacts (PyPI per-version then latest, GitHub Release) at the end.
# Collapses the manual tag->watch->rerun->verify babysitting into one command.
release-watch:
ifndef VERSION
	@echo "usage: make release-watch VERSION=<x.y.z>"
	@exit 1
endif
	@REPO=just-buildit/just-makeit scripts/release-watch.sh "$(VERSION)"

# Full release from a green, merged main: tag then watch+verify in one go.
# Named `ship` (not `release`) to avoid the C-project convention where
# `make release` is a cmake Release build.
ship: tag-release release-watch

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
	@echo "  make lint          run pre-commit hooks on all files (CI gate)"
	@echo "  make format        auto-fix formatting (ruff + mdformat)"
	@echo "  make build         build wheel → dist/"
	@echo "  make docs          build docs → site/"
	@echo "  make docs-serve    build and serve with live reload"
	@echo "  make docs-check    pre-push docs gate (strict build + docs tests)"
	@echo "  make install       install dev dependencies (uv sync)"
	@echo "  make setup         one-time: uv sync + pre-commit install"
	@echo "  make bump-version VERSION=x.y.z  update version in pyproject.toml"
	@echo "  make check-version VERSION=x.y.z verify version matches"
	@echo "  make release-branch VERSION=x.y.z create release branch"
	@echo "  make tag-release VERSION=x.y.z   tag + push to trigger release"
	@echo "  make release-watch VERSION=x.y.z watch release.yml + verify artifacts"
	@echo "  make ship VERSION=x.y.z          tag-release then release-watch"
	@echo "  make clean         remove build artifacts"
	@echo "  make examples-clean  remove build artifacts from all examples"
	@echo ""
