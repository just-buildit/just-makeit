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
# The coverage invocation used to exist only inside ci.yml, so the one command
# gating every merge was the one command a developer could not reproduce —
# exactly the drift this file exists to prevent (gh-716). The standard already
# owns the `coverage` / `coverage-gate` rules; only the commands belong here.
HAS_COVERAGE = 1

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
# C/C++ sources clang-format owns, and the two trees it must never touch.
# templates/ holds /*<<token>>*/ C that is only valid once rendered;
# tests/fixtures/doxygen holds headers whose BYTE-EXACT shape is the input to
# the derivation corpus (gh-649), so reformatting one silently changes what the
# parser is being asked to parse. Copied verbatim from the pre-commit mirror
# this replaced — the file selection is behaviour, not incidental.
C_INCLUDE_RE  = \.(c|h|cc|cpp|hpp)$$
C_EXCLUDE_RE  = ^(src/just_makeit/templates/|tests/fixtures/doxygen/)
# cmake-format runs ONLY over the CMake templates, and skips the three whose
# leading <<placeholder>> tokens its tokenizer rejects outright.
#
# The `\.cmake$$` anchor is load-bearing and was NOT in the mirror's `files:`
# regex — pre-commit's own `cmake-format` hook declares `types: [cmake]`, and
# that implicit filter is what kept `package.pc.in` (a pkg-config template
# living in this directory) away from a CMake parser. Selecting by directory
# alone fed it in and cmake-format died with an InternalError traceback.
# `.cmake.in` is deliberately included: it IS CMake, and formats cleanly.
CMAKE_INCLUDE_RE = ^src/just_makeit/templates/cmake/.*\.cmake(\.in)?$$
CMAKE_EXCLUDE_RE = CMakeLists_(component|module|object_core)\.cmake

# ── lint-<tool> dispatch ─────────────────────────────────────────────────────
# LINT_TOOLS stamps out one `lint-<tool>` target each; .pre-commit-config.yaml
# calls `make -s lint-<tool>` so a hook can never run a tool differently from
# the way `make format` runs it. FORMAT_TOOLS is the subset `format` runs, in
# order — ruff-format first, since a fix can invalidate a reformat.
#
# EVERY hook that runs a Python tool dispatches here, including the two local
# scripts: they used to carry `entry: python3 scripts/<x>.py` in the hook
# config, which is the same drift one layer over. Bare `python3` is whatever
# is on PATH rather than the locked dev env (sync_version.py needs tomllib, so
# on a 3.9/3.10 PATH python it did not run at all), and with the command living
# only in the hook there was no way to run by hand what pre-commit runs.
#
# clang-format and cmake-format are here for the same reason, and they were the
# last two exceptions. They ran from upstream pre-commit mirrors, so the make
# map had no entry for them — and the make-ssot hook DERIVES that map from the
# makefiles, which meant a raw `clang-format -i` on generated C was silently
# ALLOWED while `ruff check .` was denied. In a repo whose entire C surface is
# generated, that is the ungated command that matters most.
LINT_TOOLS   = ruff ruff-format mdformat clang-format cmake-format \
               sync-version assemble-examples
# `format` is the auto-fixer, so sync-version is deliberately NOT here: it
# exits 1 when it rewrites jb.toml (pre-commit's "re-stage me" convention),
# and a fixer that fails because it fixed something is a trap. assemble-examples
# returns 0 either way, and must stay LAST for the same reason it is last in
# the hook config -- it inlines scripts ruff-format may have just rewrapped.
FORMAT_TOOLS = ruff-format ruff mdformat clang-format cmake-format \
               assemble-examples

CLANG_FORMAT = $(DEV_RUN) clang-format
CMAKE_FORMAT = $(DEV_RUN) cmake-format

LINT_ruff        = $(RUFF) check --fix --unsafe-fixes $(RUFF_PATHS)
LINT_ruff-format = $(RUFF) format $(RUFF_PATHS)
LINT_sync-version = $(DEV_RUN) python scripts/sync_version.py

# `git ls-files` rather than a directory walk, so .venv/ and every build tree
# are excluded by virtue of being untracked — the mirror hook needed an
# explicit `\.venv/` exclusion for exactly that reason.
define LINT_clang-format
@git ls-files \
    | grep -E '$(C_INCLUDE_RE)' \
    | grep -Ev '$(C_EXCLUDE_RE)' \
    | xargs -r $(CLANG_FORMAT) -i
endef

define LINT_cmake-format
@git ls-files \
    | grep -E '$(CMAKE_INCLUDE_RE)' \
    | grep -Ev '$(CMAKE_EXCLUDE_RE)' \
    | xargs -r $(CMAKE_FORMAT) -i
endef

# Whole-tree, like every other tool here. The hook used to pass pre-commit's
# staged paths, so it re-assembled only the examples a commit touched -- which
# can pass while the whole-tree CI check (`assemble.py --check`) fails on the
# same commit, the exact split this dispatch exists to close. Measured cost of
# assembling all of them instead: 0.27s.
define LINT_assemble-examples
@git ls-files 'src/just_makeit/examples/*/.steps/*' \
    | xargs -r $(DEV_RUN) python scripts/assemble_examples.py
endef

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
PYTEST_DEPS     = --with pytest --with pytest-xdist --with numpy
PYTEST_ISOLATED = $(UV) run --no-project $(PYTEST_DEPS) $(JM_RUNTIME_DEPS) \
                  --with just-buildit
PYTEST          = $(PYTEST_ISOLATED) pytest
PYTEST_B        = $(PYTEST_ISOLATED) --with pytest-benchmark pytest
PYTEST_EXAMPLES = $(UV) run $(PYTEST_DEPS) pytest

# pytest-xdist. Measured on an 8-core box, 2026-08-02: the unit suite went
# 299s -> 134s and coverage 419s -> 136s, so instrumentation is nearly free
# once the work is spread. `auto` sizes to the runner instead of hard-coding a
# core count. `--dist load` beat `--dist loadscope` (120s vs 146s): the
# class-scoped fixtures here are not expensive enough to pay for the coarser
# balancing loadscope buys.
#
# NOT applied to BENCH_* below. pytest-benchmark refuses to run under xdist at
# all ("Can't have both --benchmark-only and --benchmark-disable"), which is
# also why `jm bench` scrubs PYTEST_XDIST_WORKER from the pytest it spawns.
PYTEST_PARALLEL = -n auto --dist load

# tests/test_examples.py scaffolds and builds every bundled example end to end.
# It is a regression check that the examples still work, not a source of
# coverage — so `test` and `coverage` both skip it, through one variable so
# the two cannot come to disagree about what "the examples" are. Measured
# cost of including it: 70 statements, with the reported percentage unchanged
# at 90% either way.
EXAMPLES_IGNORE = --ignore=tests/test_examples.py

# `test` is the default suite and, in a Python-only repo, IS the Python suite —
# named once here rather than defined twice.
TEST_PYTHON_CMD   = $(PYTEST) $(PYTEST_PARALLEL) -v $(EXAMPLES_IGNORE)
TEST_CMD          = $(TEST_PYTHON_CMD)
TEST_FAST_CMD     = $(PYTEST) $(PYTEST_PARALLEL) -x -q
TEST_EXAMPLES_CMD = $(PYTEST_EXAMPLES) tests/test_examples.py -v

TEST_ALL_DEPS = test test-examples

# `gates` answers "will this pass" before you push, so it has to BE the set CI
# requires. It was not: it read `lint docs-check test-all`, which omitted
# `coverage-gate` — the one gate that blocks a merge on a number — while
# including `docs-check`, which no CI job runs under that name. Wrong in both
# directions, and nothing invoked it (not CI, not scripts, not docs), so the
# drift was free to happen. A target advertised as the merge gate that is not
# one is worse than no target, because someone trusts it.
#
# The members are named directly rather than via `test-all`, so this list can
# be compared to ci.yml's `ci-passed` needs mechanically — which
# tests/test_lint_ssot.py now does, so it cannot drift back.
#
# `docs-check` stays, and is the one entry here that is NOT a required check:
# docs.yml runs the same strict build, but only `CI passed` is required, so a
# broken docs build cannot block a merge. Running it locally is the only place
# that gets caught.
GATES_DEPS    = lint test test-examples coverage-gate bench docs-check

# Setup, not gates. `gates-check` requires every `make <target>` CI runs to be
# reachable from `gates`, and it caught both of these the moment ci.yml started
# calling them — which is the gate working: naming them here is a decision,
# where leaving them out of ci.yml entirely would have been an accident.
GATES_PROVISION = install-deps install-deps-dev tool-install setup

# ── Coverage ─────────────────────────────────────────────────────────────────
# Two commands because a report is not a gate — the standard splits them so CI
# can call the one that fails, rather than producing a report and hoping
# somebody reads it. That was the real state before gh-716: `-q` kept the
# percentage out of every CI log, so the only gate was remembering to open
# Codecov.
#
# COVERAGE_MIN sits ~1 point under the measured 87.96% (Codecov, 2026-08-02):
# tight enough to catch a real regression, loose enough that ordinary churn
# does not flap the build. Raise it when the number moves up and holds; a
# threshold that only ever ratchets down is not a gate either.
#
# Left at 87 deliberately while the run configuration changes underneath it:
# parallelising and dropping the examples moved the measured number to 90%, but
# changing the speed and the threshold in one step means a red build tells you
# nothing about which did it. Raise it once CI has reported 90% a few times.
COVERAGE_MIN     ?= 87
COVERAGE_REPORTS  = --cov=just_makeit --cov-report=xml --cov-report=term \
                    --junitxml=junit.xml -o junit_family=legacy
COVERAGE_BASE     = $(DEV_RUN) pytest $(PYTEST_PARALLEL) $(EXAMPLES_IGNORE) \
                    $(COVERAGE_REPORTS)
COVERAGE_CMD      = $(COVERAGE_BASE)
COVERAGE_GATE_CMD = $(COVERAGE_BASE) --cov-fail-under=$(COVERAGE_MIN)

# ── Build ────────────────────────────────────────────────────────────────────
# No WHEEL_CMD override: standard.mk's default is `uv build --wheel`, which is
# what release.yml publishes with. There used to be one here
# (`PYTHONPATH=src ... --no-build-isolation`), so `make wheel` and the release
# built the wheel two different ways and nobody could have noticed — it carried
# no justification back to the commit that added it, and the two forms produce
# a byte-identical artifact (sha256 9699cd5c…). Deleting it is what lets
# release.yml call the target instead of repeating the command.

# ── Docs ─────────────────────────────────────────────────────────────────────
# The strict build catches broken TOC anchors (which the test suite does NOT),
# and tests/test_docs.py catches mangled MkDocs tab blocks + other invariants.
DOCS_PREPARE = $(PYTHON) scripts/copy_examples.py

# `install.sh` is served from the Pages site root — it is what
# `curl -fsSL <site>/install.sh` fetches — so it is part of the site, not part
# of deployment. docs.yml used to copy it in as its own step, which made a
# locally-built `site/` quietly different from the one CI deploys: the file
# only existed in CI. Here it is in the build, so `make docs` and `make
# docs-serve` produce the real site.
#
# It has to run AFTER the build rather than in DOCS_PREPARE, because the
# build's `--clean` wipes `site/` first.
define DOCS_BUILD_CMD
$(ZENSICAL) build --clean --strict
cp install.sh site/install.sh
endef

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

# `clean` used to leave every example's build artifacts behind, so a full clean
# was two commands and you had to know the second one existed. `examples-clean`
# stays a target in its own right (local.mk explains why it is local rather
# than standard) — calling it from here just means `make clean` is complete.
define CLEAN_CMD
find src -name "*.pyc" -delete
find src -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
$(MAKE) -s examples-clean
endef

include standard.mk
