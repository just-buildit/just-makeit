# Repo-local make targets. standard.mk includes this if present, and it may
# only ADD targets — redefining a standard one makes it the fork the standard
# exists to prevent. Anything named here goes into LOCAL_TARGETS, which puts it
# in `.PHONY`, in `make help`, and under the same gates as a standard target.
#
# `examples-clean` is just-makeit's alone. It does NOT belong in HAS_EXAMPLES:
# doppler has examples too, but cleans them from its own `clean`, so a required
# EXAMPLES_CLEAN_CMD would force it either to give up `test-examples` or to
# invent a command for a target it does not want — a fake target advertised in
# `help`, which is the ghost shape one level up.
LOCAL_TARGETS = start-here examples-clean pr-watch install-deps-dev tool-install \
                conflict-check \
                complex-spelling-check \
                coverage-subprocess-check gates-index gates-index-update \
                gates-declared-check \
                doppler-pin-check consumer-smoke

# The entry point for someone new to this repo. It is a SIGNPOST, not a copy:
# every line either links to the source that owns that answer, or reports state
# measured right now. Nothing here restates content that lives elsewhere,
# because a second copy of the rules is the drift this whole standard exists to
# prevent — and it would be a copy nobody thinks to check.
#
# That is also why it names only `help`, the one target the standard
# guarantees, and takes the canonical URL from the variable the drift gate
# actually fetches rather than hardcoding it.
#
# The readiness block is the part a document genuinely cannot do: it answers
# "what do I still need to do", for you, at the moment you ask.
#
# Local for now. If doppler wants one too, criterion 10 says it stops being
# local and moves into the standard.
start-here: ## Start here: where the answers live, and what you still need
	@echo ""
	@echo "  just-makeit — development"
	@echo ""
	@echo "  Every target that exists   make help"
	@echo "  This repo                  docs/developers/START_HERE.md"
	@echo "  The shared targets         $(if $(STANDARD_URL),$(STANDARD_URL),(drift gate off))"
	@echo "    vendored, never edited here; usage and the full contract:"
	@echo "    https://github.com/just-buildit/just-buildit.github.io#using-standardmk"
	@echo ""
	@$(MAKE) -s gates-index
	@echo "  Readiness"
	@hook=$$(git rev-parse --git-path hooks/pre-commit 2>/dev/null); \
	 if [ -n "$$hook" ] && [ -f "$$hook" ]; then \
	     echo "    ok    git hook installed"; \
	 else \
	     echo "    todo  git hook missing            -> make setup"; \
	 fi
	@if $(RUFF) --version >/dev/null 2>&1; then \
	     echo "    ok    dev tools available"; \
	 else \
	     echo "    todo  dev tools not synced        -> make setup"; \
	 fi
	@if $(MAKE) -s standard-check >/dev/null 2>&1; then \
	     echo "    ok    standard.mk matches canonical"; \
	 else \
	     echo "    todo  standard.mk drifted or unreachable -> make lint"; \
	 fi
	@echo ""

examples-clean: ## Remove build artifacts from every example
	@for d in examples/*/; do \
	    [ -f "$$d/Makefile" ] && $(MAKE) -C "$$d" clean 2>/dev/null || true; \
	done
	find examples -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true

# Report a PR's check outcome. It is NOT the merge gate — `gh pr merge --auto`
# is, and it evaluates the required set server-side where it cannot be got
# wrong. This only answers "did it land, or is it actually stuck", so a failure
# gets noticed instead of waited on. See scripts/pr-watch.sh for the three ways
# a hand-rolled version of this poll silently reports green.
pr-watch: ## Report whether PR=<n> landed or is genuinely failing
ifndef PR
	@echo "usage: make pr-watch PR=<number>"; exit 1
endif
	@REPO=just-buildit/just-makeit scripts/pr-watch.sh $(PR)

# ── the two CI reached around the Makefile for ──────────────────────────────
# Both existed only as raw commands in ci.yml. skills://make-ssot: if a target
# does not exist for what you need, that is a gap in the Makefile — reaching
# around it just moves the drift somewhere nobody looks.

# `install-deps` (standard) installs bootstrap.toml's system packages with no group
# and no source selector, which is why ci.yml called `jbx` directly for the dev
# group and picked apt/brew in YAML. This ADDS a sibling rather than
# redefining the standard target — local.mk may only add, and make would warn
# about the override anyway.
#
# The source follows the OS, chosen here rather than in a workflow `if:`, so
# the choice lives with the command it qualifies.
INSTALL_DEPS_GROUP  ?= dev
INSTALL_DEPS_SOURCE ?= $(if $(filter Darwin,$(shell uname -s)),brew,apt)

install-deps-dev: ## Install the dev-group system packages (bootstrap.toml)
	@command -v jbx >/dev/null 2>&1 \
	    || curl -sSL https://just-buildit.github.io/get-jb.sh | bash
	PATH="$$HOME/.local/bin:$$PATH" \
	    jbx install-deps -g $(INSTALL_DEPS_GROUP) -s $(INSTALL_DEPS_SOURCE)

# `uv tool install .` is the ONLY check that this package installs on every
# OS x Python leg, so it is load-bearing — and it was hand-rolled in ci.yml
# three times, retry loop and all. A transient curl SSL blip has failed it
# (exit 35, macOS), so the retry is real; three copies of it are not.
TOOL_INSTALL_ATTEMPTS ?= 3

tool-install: ## Install this package as a uv tool (retries a network flake)
	@for attempt in $$(seq 1 $(TOOL_INSTALL_ATTEMPTS)); do \
	    $(UV) tool install . && exit 0; \
	    echo "uv tool install attempt $$attempt failed (transient?); retrying…"; \
	    sleep 10; \
	done; \
	echo "::error::uv tool install failed after $(TOOL_INSTALL_ATTEMPTS) attempts"; \
	exit 1

# gh-974: a merge-conflict marker that reached the published docs site and sat
# there for ten days and a release. The check lives in a script rather than in
# this recipe so its own gate can run it over seeded files — a lint target that
# can only be exercised by corrupting the repo is a target nobody proves.
#
# Hung off `lint` because CI runs `make lint` and nothing
# else, and a rule in the Makefile is a rule tests/test_lint_ssot.py rejects.
lint: conflict-check

conflict-check: ## Fail on a merge-conflict marker in a tracked text file
	@scripts/conflict-check.sh

# gh-1246 changed generated C to the `_Complex` spelling. The sweep that came
# with it covered tests/, examples/ and docs/; nobody looked in .github/, and
# artifact.yml's pre-publish smoke patches a stub through a regex anchored on
# the old one. It matched nothing and failed all TWELVE legs -- in the v0.74.0
# RELEASE run, after the tag was pushed, not in a PR. A second copy of the same
# logic was in the shipped scripts/docker-e2e.sh.
lint: complex-spelling-check

complex-spelling-check: ## Fail on the pre-gh-1246 `complex` spelling outside its allow-list
	@scripts/complex-spelling-check.sh

# ADVISORY, and hung off `lint` so it is seen on every PR without gating one.
# The pin drifts because doppler published, not because of the change being
# linted -- measured 2026-08-30, five doppler releases in thirty days -- so a
# hard gate here would redden this repo weekly over someone else's cadence.
# That is the `standard-check` shape, and this repo already knows what it
# costs. What actually protects the example is `test_example[nco_tone]`.
lint: doppler-pin-check

doppler-pin-check: ## Report when the nco_tone doppler pin lags latest (advisory)
	@python3 scripts/check_doppler_pin.py

# gh-978: hung off `coverage-gate` rather than `lint`, because that is the
# target whose environment it is about — and the one CI runs with pytest-cov
# present. A pytest test could not host this: `make test` runs the suite
# without that plugin, so the check would skip, and a skip is not a pass.
#
# COVERAGE_ENV is passed in rather than defaulted inside the script, so this
# proves the Makefile's own value works. Break COVERAGE_ENV and this goes red.
coverage-gate: coverage-subprocess-check

coverage-subprocess-check: ## Prove a CLI-driven test counts toward coverage
	@$(COVERAGE_ENV) $(DEV_RUN) sh scripts/coverage-subprocess-check.sh

# The obligations this repo's gates enforce, printed from the gates.
#
# A gate teaches at the moment it fires, and by then the work is done. Five
# times in one session a change was correct and its surrounding contract was
# not, and every one was caught late. This is the same list, up front — at
# session start, via ~/.claude/hooks/maintainer-role.sh, which is the one
# moment it can still change what someone does.
#
# DERIVED, never written: each gate declares its own obligation where it
# lives, so a catalogue cannot drift from the gates it claims to describe.
# Which files *could* declare one is not derivable — measured, not assumed:
# the "scans the repo" tell picks 109 files loosely and 14 tightly, and the
# 14 miss four of the five that actually caught something. So a human
# declares and the RATCHET refuses shrinkage, which is this repo's idiom for
# a judgement no predicate can make.
lint: gates-declared-check

gates-index: ## The obligations this repo's gates enforce
	@python3 scripts/gates-index.py

gates-index-update: ## Record the declared gates as the ratchet's new floor
	@python3 scripts/gates-index.py --update

gates-declared-check: ## Verify no gate has dropped its declared obligation
	@python3 scripts/gates-index.py --check

# gh-1590: the acceptance test for epic gh-1584 -- jm packages installed the
# documented way and consumed by the official pkg-config and CMake
# instructions. Here it installs to a temp prefix (or PREFIX) with the
# documented hints; CI sets CONSUMER_SMOKE_DEFAULT_PREFIX=1 on a throwaway
# runner to install to the default prefix and consume with no hints at all.
consumer-smoke: ## Install jm packages, consume them by the official instructions
	bash scripts/consumer-smoke.sh
