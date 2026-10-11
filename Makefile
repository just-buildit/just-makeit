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
# An entry is a file, changelog.d/<section>/<slug>.md, so parallel PRs stop
# conflicting on CHANGELOG.md (gh-1526); release-branch promotes them. The
# flag is what turns both of the standard's changelog gates on, so their
# obligations are declared here, where turning it off would drop them.
# GATE: a change under src/ carries a changelog.d/ fragment.
# GATE: a branch never edits a CHANGELOG section that already shipped.
HAS_CHANGELOG        = 1
CHANGELOG_CODE_PATHS = src/just_makeit

# The CI toolchain image (gh-1796), the org standard's shared one
# (just-buildit.github.io#86): the Linux CI legs run in it, pinned with every
# input it was built from in .github/ci-images.env. The flag vendors
# docker/ci.Dockerfile, scripts/ci-image.py and .github/workflows/ci-image.yml,
# and hangs `ci-image-check` off lint, so a PR moving bootstrap.toml or the
# Dockerfile cannot merge on the old image. jm's own copy of all three
# (gh-1797) is gone: one implementation, in canonical.
# GATE: the pinned CI image was built from this tree's image sources.
HAS_CI_IMAGE   = 1
CI_IMAGE_REPO  = ghcr.io/just-buildit/jm-ci
CI_IMAGE_BASES = ubuntu:24.04
CI_IMAGE_GROUPS = dev

# `make pr-watch PR=<n>` (gh-1818): the standard's target, and the flag vendors
# scripts/pr-watch.sh, so standard-check holds it to canonical. jm's own
# target ran a hand copy that had already missed canonical's REPO derivation
# and its stuck-queued-run detector (gh-1812). Nothing to configure: REPO
# derives from origin.
HAS_PR_WATCH = 1

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

# gh-1625: the jm a recipe drives is THIS checkout's, named once here. A bare
# `just-makeit` is whichever is first on PATH -- a stale `uv tool` install on
# a developer's box -- so `make consumer-smoke` tested a jm fifteen releases
# old and reported on it. Absolute, so it means the same from any directory a
# recipe cds into; `--no-project` keeps the dev group out, as PYTEST does.
# tests/test_gh1625_tree_jm.py refuses a recipe or script that names a bare
# jm instead.
JM         = $(UV) run -q --no-project --with-editable $(CURDIR) just-makeit

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
# this replaced — the file selection is behaviour, not incidental. The third,
# examples/stale_project/tree, is a project frozen at jm 0.33.14 (gh-1443):
# reformatting it would make it a different, newer project.
C_INCLUDE_RE  = \.(c|h|cc|cpp|hpp)$$
C_EXCLUDE_RE  = ^(src/just_makeit/templates/|tests/fixtures/doxygen/|src/just_makeit/examples/stale_project/tree/)
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
               sync-version assemble-examples uv-lock
# `format` is the auto-fixer, so sync-version is deliberately NOT here: it
# exits 1 when it rewrites bootstrap.toml (pre-commit's "re-stage me" convention),
# and a fixer that fails because it fixed something is a trap. assemble-examples
# returns 0 either way, and must stay LAST for the same reason it is last in
# the hook config -- it inlines scripts ruff-format may have just rewrapped.
FORMAT_TOOLS = ruff-format ruff mdformat clang-format cmake-format \
               assemble-examples

CLANG_FORMAT = $(DEV_RUN) clang-format

# cmakelang -- cmake-format here, cmake-lint in the tests -- runs under a
# PINNED interpreter, never the Python under test (gh-1930). Its last release
# (2020) builds its lexer from an `re.Scanner` with capturing groups, which
# CPython 3.15 refuses, so under 3.15 every entry point dies on its first
# file. 3.14 still accepts them.
#
#   --no-project  keeps the project env out: a `--python` in project mode
#                 REPLACES .venv with that interpreter's.
#   --python      wins over the UV_PYTHON a CI leg's setup-uv exports.
#   [yaml]        cmake-format reads .cmake-format.yaml only with it; the dev
#                 env had pyyaml only by accident, through pre-commit.
#
# The version is the dev group's pin, read from pyproject.toml, never restated
# here: its quoted list entry, `"cmakelang==X",`, so prose naming the tool
# cannot match. Taken apart by make's own functions, leaving the one $(shell)
# with no quote, backslash or glob in it: GNU make on Windows hands $(shell)
# to sh through a Windows command line, and a sed program arrived there
# mangled (`multiple p options`, on the clang-cl job). The tests get
# CMAKE_LINT from the recipe that runs them (PYTEST_EXAMPLES), so they run
# this command, not a second spelling of it.
# tests/test_gh1930_cmakelang_pinned_python.py holds all of it.
comma            := ,
CMAKELANG_PYTHON  = 3.14
CMAKELANG_PIN     = $(subst ",,$(subst $(comma),,$(filter "cmakelang==%, \
                        $(shell grep -F cmakelang== pyproject.toml))))
CMAKELANG_REQ     = $(subst cmakelang,cmakelang[yaml],$(CMAKELANG_PIN))
CMAKELANG         = $(UV) run -q --no-project --python $(CMAKELANG_PYTHON) \
                    --with "$(CMAKELANG_REQ)"
CMAKE_FORMAT      = $(CMAKELANG) cmake-format
CMAKE_LINT        = $(CMAKELANG) cmake-lint

LINT_ruff        = $(RUFF) check --fix --unsafe-fixes $(RUFF_PATHS)
LINT_ruff-format = $(RUFF) format $(RUFF_PATHS)
# `--no-sync`: sync_version writes uv.lock's version line, so the uv that
# runs it must not re-lock first. A plain `uv run` re-locks a lock that is
# behind pyproject.toml's version -- rewriting the whole file and stamping
# the running uv's lockfile `revision` on it -- before the script starts
# (gh-1866, measured on uv 0.11.28, 0.12.21 and 0.12.23). `--no-sync`
# neither locks nor syncs: the script needs only the stdlib (tomli below
# 3.11), so the env as it stands is enough, and the gate can run this exact
# line offline. `make bump-version` runs it too.
LINT_sync-version = $(UV) run --no-sync python scripts/sync_version.py
# Re-locks, as astral's upstream hook did; pre-commit fails the commit when
# that rewrites uv.lock, so CI's `make lint` refuses a stale lock. Not in
# FORMAT_TOOLS: re-resolving is not formatting.
LINT_uv-lock      = $(UV) lock

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
# pytest runs three ways. The deltas are spelled out here rather than in three
# parallel command strings that drift independently:
#   PYTEST          unit suite — `--no-project` keeps the project env OUT, so
#                   the suite exercises the installed-package path; the
#                   generated projects it scaffolds build with just-buildit.
#   PYTEST_B        the same, plus pytest-benchmark.
#   PYTEST_EXAMPLES example builds — deliberately WITHOUT `--no-project`, since
#                   these need `just-makeit` itself importable from the project
#                   env (just-buildit arrives transitively as its build dep), so
#                   its runtime deps arrive with it. Handed CMAKE_LINT, the
#                   one cmake-lint command, as consumer-smoke is handed JM
#                   (gh-1930): the tests' helper refuses to run without it.
# pyyaml: gh-851's pre-publish artifact gate READS `.github/workflows/`,
# and skipped itself without it -- one of the green skips gh-1442 turned
# up. It belongs here rather than in the dev group: this is test
# infrastructure like pytest and numpy, not a formatter whose exact
# version has to agree byte-for-byte across machines.
PYTEST_DEPS     = --with pytest --with pytest-xdist --with numpy \
                  --with pyyaml --with pytest-benchmark
PYTEST_ISOLATED = $(UV) run --no-project $(PYTEST_DEPS) --with just-buildit \
                  --with-editable .
PYTEST          = $(PYTEST_ISOLATED) pytest
PYTEST_B        = $(PYTEST_ISOLATED) --with pytest-benchmark pytest
PYTEST_EXAMPLES = CMAKE_LINT='$(CMAKE_LINT)' $(UV) run $(PYTEST_DEPS) pytest

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
# Tests that need the PROJECT env, not the isolated one.
#
# `PYTEST` runs `uv run --no-project` deliberately, so the suite exercises
# the installed-package path. The cost was never written down: every
# `skipif` asking the LIVE ENVIRONMENT a question gets the ISOLATED env's
# answer. A test gated on a dev-group tool -- mypy, ruff, clang-format,
# cmake-lint -- therefore skips in CI and RUNS for a developer whose venv
# is activated, which is the worst way round. 31 tests in the five files
# below were inert on every CI run and green on every laptop (gh-1442).
#
# `PYTEST_EXAMPLES` is already `--no-project`-free, so they belong on it.
#
# ONE list, driving both the ignore and the run. The comment above used to
# claim that and was not true: `TEST_EXAMPLES_CMD` named the file a second
# time, so "the examples" was already two declarations that nothing held
# equal.
PROJECT_ENV_TESTS = tests/test_examples.py \
                    tests/test_gh1601_install_components.py \
                    tests/test_gh1591_c_prefix_nm.py \
                    tests/test_gh1653_upgrade_builds.py \
                    tests/test_gh1657_prefix_collision.py \
                    tests/test_gh1651_class_name_imports.py \
                    tests/test_gh1599_public_flags.py \
                    tests/test_stub_conformance.py \
                    tests/test_gh1724_array_stub_mypy.py \
                    tests/test_gh746_py_format_command.py \
                    tests/test_gh746_formatter_fixed_point.py \
                    tests/test_gh758_format_convergence.py \
                    tests/test_cmake_lint.py \
                    tests/test_c_style.py \
                    tests/test_gh745_c_format_command.py \
                    tests/test_gh958_c_style_is_outcome_neutral.py \
                    tests/test_gh1219_impl_marker_wraps.py \
                    tests/test_gh1448_token_survives_clang_format.py \
                    tests/test_gh1734_array_arg_clang_format.py \
                    tests/test_gh1452_consumer_links_installed_tree.py \
                    tests/test_gh1572_combined_lib_links_extra.py \
                    tests/test_gh1573_pc_requires_private.py \
                    tests/test_gh1576_dep_usage_both_faces.py \
                    tests/test_gh1584_consumer_matrix.py \
                    tests/test_gh1583_schema8_consumer.py \
                    tests/test_gh1376_presets_build.py \
                    tests/test_gh1443_gate_a.py \
                    tests/test_gh1489_token_survives_ruff.py \
                    tests/test_gh1478_ruff_clean_scaffold.py

EXAMPLES_IGNORE = $(addprefix --ignore=,$(PROJECT_ENV_TESTS))

# `test` is the default suite and, in a Python-only repo, IS the Python suite —
# named once here rather than defined twice.
TEST_PYTHON_CMD   = $(PYTEST) $(PYTEST_PARALLEL) -v $(EXAMPLES_IGNORE)
# The examples whose GOLDEN records what jm's generated code and drift
# reports look like to an upgrading project. `test-examples` alone ran
# them, so an emitter change passed `make test` and went red only in CI:
# gh-1700 and gh-1710 both did, on every platform, on 2026-09-29. One
# example, ~10 s, so it rides in `test` rather than waiting for CI.
GOLDEN_EXAMPLES   = stale_project
TEST_GOLDENS_CMD  = $(PYTEST_EXAMPLES) tests/test_examples.py -v \
                    -k "$(GOLDEN_EXAMPLES)"
TEST_CMD          = $(TEST_PYTHON_CMD) && $(TEST_GOLDENS_CMD)
TEST_FAST_CMD     = $(PYTEST) $(PYTEST_PARALLEL) -x -q
# EXAMPLES_K narrows the run with pytest -k, e.g. to one example while
# regenerating its golden (see stale_project). Empty = all.
EXAMPLES_K        ?=
TEST_EXAMPLES_CMD = $(PYTEST_EXAMPLES) $(PROJECT_ENV_TESTS) -v \
                    $(if $(EXAMPLES_K),-k "$(EXAMPLES_K)")

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
# `docs-check` is run by CI under that name since gh-1801 item 3 (ci.yml's
# `docs` job); see GATES_LOCAL_ONLY below for what it used to be.
#
# `consumer-smoke` (gh-1590) is a gate CI runs by name. Locally it installs to
# a temp prefix and consumes with the documented hints; only CI, on a
# throwaway runner, installs to the default prefix with none
# (CONSUMER_SMOKE_DEFAULT_PREFIX=1) -- so `make gates` never sudo-installs.
GATES_DEPS    = lint test test-examples coverage-gate bench docs-check \
                consumer-smoke

# `docs-check` (the strict build, then tests/test_docs.py) is a merge gate
# CI runs by name: ci.yml's `docs` job, which `CI passed` waits on (gh-1801
# item 3, gh-1782). It used to be named in GATES_LOCAL_ONLY, on the grounds
# that docs.yml ran the same build -- but docs.yml fed no required check, so
# a broken strict build merged green. docs.yml now only deploys.
GATES_LOCAL_ONLY =

# Setup, not gates. `gates-check` requires every `make <target>` CI runs to be
# reachable from `gates`, and it caught both of these the moment ci.yml started
# calling them — which is the gate working: naming them here is a decision,
# where leaving them out of ci.yml entirely would have been an accident.
# `wheel` builds the artifact ci.yml's per-PR artifact smoke installs
# (gh-1632): a build step the smoke gates, not a gate itself.
# `coverage-shard` is the same shape (gh-2078): it leaves one shard's data
# for `coverage-gate` to judge, and gates nothing alone. `make gates` runs the
# gate unsharded, over the whole suite.
GATES_PROVISION = install-deps install-deps-dev tool-install setup wheel \
                  coverage-shard

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
COVERAGE_JUNIT    = --junitxml=junit.xml -o junit_family=legacy
COVERAGE_REPORTS  = --cov=just_makeit --cov-report=xml --cov-report=term \
                    $(COVERAGE_JUNIT)

# gh-978: count the tests that drive the shipped CLI. Both paths are ABSOLUTE,
# and that is the entire fix — a test that runs `jm` in a scaffolded project
# gives the subprocess a different cwd, so a relative value resolves against
# THAT directory:
#
#   COVERAGE_FILE          data written to the tmp project, never combined,
#                          deleted with the tmpdir. The subprocess was
#                          instrumented all along; its measurements were thrown
#                          away. `tests/test_gh975_missing_cmake_anchor.py`
#                          alone went 0% -> 42% on `_status.py`; the full suite
#                          moved `_cli.py` 68% -> 78% and the total 91% -> 92%.
#   COVERAGE_PROCESS_START worse than useless relative: a scaffolded project
#                          HAS a pyproject.toml, so the subprocess read the
#                          generated project's config instead of this one.
#
# Here rather than in ci.yml (where COVERAGE_PROCESS_START used to live alone)
# so `make coverage` measures locally exactly what CI measures. The gate that
# holds it is `coverage-subprocess-check` in local.mk.
COVERAGE_ENV      = COVERAGE_PROCESS_START=$(CURDIR)/pyproject.toml \
                    COVERAGE_FILE=$(CURDIR)/.coverage
COVERAGE_RUN      = $(COVERAGE_ENV) $(DEV_RUN) pytest $(PYTEST_PARALLEL) \
                    $(EXAMPLES_IGNORE)
COVERAGE_BASE     = $(COVERAGE_RUN) $(COVERAGE_REPORTS)
COVERAGE_CMD      = $(COVERAGE_BASE)

# gh-2078: CI runs the suite as COVERAGE_SHARDS jobs, then gates ONCE.
#
# Measured on CI before the split (draft #2082): Coverage took 41.7 min of
# its 45-min timeout on an AMD EPYC 7763, and 42 of 55 PR-head runs
# (2026-10-05..08) took 28 min or more. All of it was pytest; combining the
# ~2000 per-process data files took <= 35 s.
#
#   make coverage-shard COVERAGE_SHARD=K   one job per K in 1..N. Runs the
#       test FILES that shard K owns (tests/_shard.py: a SHA-1 of the path,
#       so complete, disjoint and registration-free) and leaves its data and
#       junit.xml in COVERAGE_SHARD_ROOT/K/. No threshold: half a suite's
#       percentage means nothing.
#   make coverage-gate COVERAGE_FROM_SHARDS=1   combines every shard's data
#       under COVERAGE_SHARD_ROOT, refuses unless exactly N are there (one
#       missing shard can still clear the threshold on its own), and applies
#       COVERAGE_MIN to the union.
#
# Plain `make coverage-gate` is unchanged: the whole suite in one run, gated
# -- what `make gates` runs. Locally, CI's path is the two lines above with
# K = 1..N in turn. The coverage CLI runs WITHOUT COVERAGE_ENV: with
# COVERAGE_PROCESS_START set, `coverage combine` would measure itself and
# leave a data file of its own.
COVERAGE_SHARDS      = 2
COVERAGE_SHARD       =
COVERAGE_SHARD_ROOT  = coverage-shards
COVERAGE_FROM_SHARDS =
COVERAGE_SHARD_DIR   = $(COVERAGE_SHARD_ROOT)/$(COVERAGE_SHARD)
COVERAGE_SHARD_CMD   = $(COVERAGE_RUN) \
                       --jm-shard=$(COVERAGE_SHARD)/$(COVERAGE_SHARDS) \
                       --cov=just_makeit --cov-report= $(COVERAGE_JUNIT)
COVERAGE_SHARD_DATA  = $(wildcard $(COVERAGE_SHARD_ROOT)/*/.coverage)
COVERAGE_COMBINE_CMD = n=$(words $(COVERAGE_SHARD_DATA)); \
    if [ "$$n" -ne $(COVERAGE_SHARDS) ]; then \
        echo "coverage-gate: $$n shard data files under" \
             "$(COVERAGE_SHARD_ROOT)/, want $(COVERAGE_SHARDS)"; \
        exit 1; \
    fi; \
    $(DEV_RUN) coverage combine $(COVERAGE_SHARD_DATA) && \
    $(DEV_RUN) coverage xml && \
    $(DEV_RUN) coverage report --precision=2 --fail-under=$(COVERAGE_MIN)

COVERAGE_GATE_CMD = $(if $(COVERAGE_FROM_SHARDS),$(COVERAGE_COMBINE_CMD),\
                    $(COVERAGE_BASE) --cov-fail-under=$(COVERAGE_MIN))

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
# bootstrap.toml's version is synced from pyproject.toml by a pre-commit hook, so it is
# a real second manifest and `version-check` probes both — a desync would
# otherwise ship silently.
define VERSION_PROBES
pyproject.toml|grep '^version = ' pyproject.toml | sed 's/.*"\(.*\)".*/\1/'
bootstrap.toml|grep '^version' bootstrap.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'
endef

# Every file a release commit touches, written HERE — not left for the
# pre-commit hooks to discover.
#
# This used to sed `pyproject.toml` alone, so the `sync-version` and `uv-lock`
# hooks then rewrote `bootstrap.toml` and `uv.lock` and **aborted the first
# `git commit` of every release**. That was survivable (re-run the identical
# commit and it passes) and it had been survived often enough to be written
# into the release runbook as expected behaviour — which is the tell that it
# had stopped being read as a defect.
#
# It was never harmless. A hook that aborts a commit does not stop the next
# command in the script, so `git commit -am ... ; git push` pushes the
# UNCHANGED head — the exact foot-gun the runbook warns about two paragraphs
# later, armed by this line. And "the release commit is four files, two means
# you pushed a half-bump" is a rule that only exists because the bump did not
# write four files.
#
# `--exit-zero` on sync_version keeps the write and drops the pre-commit
# convention of exiting 1 on change; the hook still calls it without the flag,
# so the gate is unchanged.
#
# uv.lock's version line is sync_version's to write, and uv only CHECKS
# (gh-1866). The bump ran sync_version under a plain `uv run` and then
# `uv lock`, and both re-lock: they rewrite the whole file and stamp the
# running uv's lockfile `revision` on it, so uv 0.12.22+ wrote 5 over this
# repo's 3. The release commit then changed more than the version string,
# `make ci-changes` said src=true, and the release ran the full matrix -- and
# nothing said so until CI did. Now sync_version runs as the hook runs it
# (`LINT_sync-version`, under `--no-sync`), the lock's diff is the version
# line under any uv, and a lock that needs more (a dependency moving) is
# refused HERE, where it can be re-locked in a PR of its own. Every step is
# idempotent, so re-running the bump is free. `-i.bak` is the in-place
# spelling BSD sed accepts too: a bare `-i` reads the expression as a backup
# suffix on macOS.
BUMP_VERSION_CMD = sed -i.bak \
                       's/^version = "[^"]*"/version = "$(VERSION)"/' \
                       pyproject.toml && rm -f pyproject.toml.bak && \
                   $(LINT_sync-version) --exit-zero && \
                   { $(UV) lock --check --quiet || { \
                       echo "bump-version: uv.lock needs more than its" \
                            "version line (above). A release commit is" \
                            "a version bump alone (gh-1866): re-lock in" \
                            "a PR of its own, then bump again."; \
                       exit 1; }; }
# Autonomously watch release.yml: stream job outcomes, auto-rerun ONE
# pre-publish flake (safe — publish is gated behind smoke), and verify the real
# artifacts (PyPI per-version then latest, GitHub Release) at the end.
#
# The script is VENDORED from canonical and gated by standard-check; everything
# repo-specific is here. RW_PUBLISH_JOB takes the anchored default: the loose
# `publish` this repo used to carry also matched all twelve
# "Artifact smoke (pre-publish) / …" jobs, which succeed before PyPI is touched,
# so the flake recovery below could never actually fire.
RELEASE_WATCH_CMD = REPO=just-buildit/just-makeit RW_PKG=just-makeit \
                        scripts/release-watch.sh "$(VERSION)"

# ── Clean ────────────────────────────────────────────────────────────────────
CLEAN_PATHS = dist/ site/ .pytest_cache/

# `clean` used to leave every example's build artifacts behind, so a full clean
# was two commands and you had to know the second one existed. `examples-clean`
# stays a target in its own right (local.mk explains why it is local rather
# than standard) — calling it from here just means `make clean` is complete.
define CLEAN_CMD
find src -name "*.pyc" -delete
find src -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
# gh-1027: and then the shells those leave behind. Cutting an example removes
# every tracked file, but a previous `jm example <name>` run leaves a
# gitignored __pycache__, so the parent survives holding only that. The line
# above empties it; without this one it stays, and `clean` -- the documented
# remedy -- leaves the tree in the state that was already reported as broken.
# Runs AFTER the __pycache__ sweep, which is what makes them empty.
find src/just_makeit/examples -mindepth 1 -maxdepth 1 -type d -empty -delete
$(MAKE) -s examples-clean
endef

# ── Vendored from canonical ──────────────────────────────────────────────────
# Verbatim copies the drift gate holds to canonical, alongside standard.mk
# itself. Edit canonical and re-vendor; never edit these in place.
# msvc-env.sh replaced ilammy/msvc-dev-cmd (node20, unmaintained) in the
# Windows jobs. dependabot.yml is the org's Actions-pin config, published at
# github/dependabot.yml (Pages does not serve .github/).
VENDORED_FILES = scripts/release-watch.sh scripts/msvc-env.sh \
                 .github/dependabot.yml

# ci-docs (gh-1801 item 3): what counts as docs is the standard's CI_DOCS_RE
# minus this. docs/examples/ is GENERATED -- copied from each example's
# README.md, itself assembled from .steps by lint-assemble-examples -- so a
# diff there is a code change wearing a docs path. The originals under
# src/just_makeit/examples/ are already outside CI_DOCS_RE's anchors.
CI_DOCS_EXCLUDE_RE = ^docs/examples/

include standard.mk
