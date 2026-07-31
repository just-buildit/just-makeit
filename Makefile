# just-makeit — development control centre
#
# CONFIGURATION ONLY. Every shared target lives in standard.mk, vendored from
# https://just-buildit.github.io/standard.mk and never edited in place — per-repo
# variation is the variables below, because a local edit is a fork. See the
# cross-org plan in the just-buildit/.github README.
#
# `make help` is generated from standard.mk; there is no hand-written target
# list here, because a hand-written list is how a target stays advertised after
# its rule is gone.

# ── Feature flags ────────────────────────────────────────────────────────────
# just-makeit GENERATES C, but does not build any itself — the C toolchain is
# exercised through the example projects it scaffolds, under test-examples. So
# no HAS_C: `make build` here would have nothing to build.
HAS_PYTHON   = 1
HAS_DOCS     = 1
HAS_BENCH    = 1
HAS_RELEASE  = 1
HAS_EXAMPLES = 1

PYTHON     ?= $(shell uv run --no-project python -c \
                  "import sys; print(sys.executable)" 2>/dev/null || python3)
UV          = uv
BENCH_TAG  ?= $(shell git describe --tags --dirty 2>/dev/null || date +%Y%m%d)

# ── Tooling ──────────────────────────────────────────────────────────────────
# The ONLY place a tool binary is named or given flags. Humans, the pre-commit
# hooks, and CI all reach the tools through the targets in standard.mk, so
# changing a flag is a one-line edit here rather than a hunt through the
# Makefile, .pre-commit-config.yaml, and the workflow files. Versions live in
# pyproject.toml's `dev` group and are locked by uv.lock.
#
# Corollary: do NOT invoke a linter with `uvx` (or a global install). `uvx ruff`
# resolves to whatever released today, which formats differently from the
# pinned ruff and silently rewrites unrelated files. Use `make format`.
DEV_RUN    = $(UV) run --group dev
RUFF       = $(DEV_RUN) ruff
MDFORMAT   = $(DEV_RUN) mdformat
ZENSICAL   = $(DEV_RUN) zensical
PRE_COMMIT = $(DEV_RUN) pre-commit
SYNC_CMD   = $(UV) sync --group dev

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

# ── lint-<tool> dispatch ─────────────────────────────────────────────────────
# LINT_TOOLS stamps out one `lint-<tool>` target each; .pre-commit-config.yaml
# calls `make -s lint-<tool>` so a hook can never run a tool differently from
# the way `make format` runs it. FORMAT_TOOLS is the subset `format` runs, in
# order — ruff-format first, since a fix can invalidate a reformat.
LINT_TOOLS   = ruff ruff-format mdformat
FORMAT_TOOLS = ruff-format ruff mdformat

LINT_ruff        = $(RUFF) check --fix --unsafe-fixes $(RUFF_PATHS)
LINT_ruff-format = $(RUFF) format $(RUFF_PATHS)

# mdformat needs Python >=3.10 (see pyproject's dev group). On a 3.9 dev env it
# is simply absent, so skip with a notice rather than failing — the CI lint job
# runs a modern Python and enforces it there. Same self-skip pattern as the
# mypy-backed stub-conformance gate.
define LINT_mdformat
@if $(MDFORMAT) --version >/dev/null 2>&1; then \
    git ls-files '*.md' \
        | grep -Ev '$(MD_EXCLUDE_RE)' \
        | xargs -r $(MDFORMAT); \
else \
    echo "mdformat unavailable (needs Python >=3.10) — skipping"; \
fi
endef

# ── Test ─────────────────────────────────────────────────────────────────────
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

# `test` is the default suite and, in a Python-only repo, IS the Python suite —
# named once here rather than defined twice.
TEST_PYTHON_CMD   = $(PYTEST) -v --ignore=tests/test_examples.py
TEST_CMD          = $(TEST_PYTHON_CMD)
TEST_FAST_CMD     = $(PYTEST) -x -q
TEST_EXAMPLES_CMD = $(PYTEST_EXAMPLES) tests/test_examples.py -v

TEST_ALL_DEPS = test test-examples
GATES_DEPS    = lint docs-check test-all

# ── Build ────────────────────────────────────────────────────────────────────
WHEEL_CMD = PYTHONPATH=src $(UV) build --wheel --no-build-isolation

# ── Docs ─────────────────────────────────────────────────────────────────────
# The strict build catches broken TOC anchors (which the test suite does NOT),
# and tests/test_docs.py catches mangled MkDocs tab blocks + other invariants.
DOCS_PREPARE = $(PYTHON) scripts/copy_examples.py

# A post-gate, not DOCS_CHECK_CMD: the docs tests run after the strict build,
# and the list form accumulates, so a build failure no longer hides whatever
# test_docs.py would have said about the same change.
define DOCS_CHECK_POST_CMDS
$(PYTEST) tests/test_docs.py
endef

# ── Bench ────────────────────────────────────────────────────────────────────
BENCH_CMD         = $(PYTEST_B) tests/bench_scaffold.py -v --benchmark-disable-gc
BENCH_SAVE_CMD    = $(PYTEST_B) tests/bench_scaffold.py \
                        --benchmark-save=$(BENCH_TAG) --benchmark-disable-gc
BENCH_COMPARE_CMD = $(PYTEST_B) tests/bench_scaffold.py \
                        --benchmark-compare --benchmark-disable-gc

# ── Release ──────────────────────────────────────────────────────────────────
# jb.toml's version is synced from pyproject.toml by a pre-commit hook, so it is
# a real second manifest and `version-check` probes both — a desync would
# otherwise ship silently.
define VERSION_PROBES
pyproject.toml|grep '^version = ' pyproject.toml | sed 's/.*"\(.*\)".*/\1/'
jb.toml|grep '^version' jb.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'
endef

BUMP_VERSION_CMD = sed -i 's/^version = "[^"]*"/version = "$(VERSION)"/' \
                       pyproject.toml
# Autonomously watch release.yml: stream job outcomes, auto-rerun ONE
# pre-publish flake (safe — publish is gated behind smoke), and verify the real
# artifacts (PyPI per-version then latest, GitHub Release) at the end.
RELEASE_WATCH_CMD = REPO=just-buildit/just-makeit scripts/release-watch.sh \
                        "$(VERSION)"

# ── Clean ────────────────────────────────────────────────────────────────────
CLEAN_PATHS = dist/ site/ .pytest_cache/

define CLEAN_CMD
find src -name "*.pyc" -delete
find src -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
endef

include standard.mk
