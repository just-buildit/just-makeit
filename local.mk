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
                changelog-check

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

# ── changelog-check ──────────────────────────────────────────────────────────
# A branch that changes src/ must also touch CHANGELOG.md.
#
# PER BRANCH, not per file-state, and that distinction is the whole design.
# The obvious gate -- "is [Unreleased] non-empty?" -- goes INERT the moment one
# entry exists, so every PR after the first passes for free. doppler shipped a
# public C API with no entry through exactly that hole
# (doppler-dsp/doppler#705). This asks a question whose answer changes per
# branch: did THIS work touch the changelog.
#
# It exists because the process failed silently for a whole day. v0.58.0 was
# assembled from seven merged PRs, not one of which added an entry, and the
# section had to be reconstructed at release time from context that happened to
# still be around. A fresh session would have had to reverse-engineer it from
# commit messages.
#
# INERT on main by construction: HEAD is an ancestor of the base there, the
# range is empty, and there is nothing to judge. It only has an opinion on a
# branch that is ahead.
#
# Touching the file is the bar, not growing a particular section. A refactor
# that genuinely warrants no user-facing note is one honest line from passing,
# and a gate that tries to judge which changes "deserve" an entry is a gate
# that argues with its author.
CHANGELOG_BASE ?= origin/main

# Hung off `lint`, which is what CI runs. standard.mk owns lint's recipe; this
# adds a prerequisite to it, and it lives HERE rather than in the Makefile
# because tests/test_lint_ssot.py holds the Makefile to configuration only --
# a rule there is a rule the drift gate cannot see. It caught this line in the
# Makefile on the first run.
lint: changelog-check

changelog-check: ## Verify a branch that changes src/ also touches CHANGELOG.md
	@base=$$(git merge-base HEAD $(CHANGELOG_BASE) 2>/dev/null) || { \
	    echo "changelog-check: no merge base with $(CHANGELOG_BASE) —"; \
	    echo "  fetch it (CI needs fetch-depth: 0) or set CHANGELOG_BASE."; \
	    exit 1; \
	 }; \
	 files=$$(git diff --name-only "$$base"..HEAD); \
	 if [ -z "$$files" ]; then \
	     echo "changelog-check: no commits ahead of $(CHANGELOG_BASE) — inert"; \
	     exit 0; \
	 fi; \
	 src=$$(printf '%s\n' "$$files" | grep '^src/just_makeit/' || true); \
	 if [ -z "$$src" ]; then \
	     echo "changelog-check: no src/ changes on this branch"; \
	     exit 0; \
	 fi; \
	 if printf '%s\n' "$$files" | grep -qx 'CHANGELOG.md'; then \
	     echo "changelog-check: src/ changed and CHANGELOG.md was updated"; \
	     exit 0; \
	 fi; \
	 echo "ERROR: this branch changes src/ but not CHANGELOG.md:"; \
	 printf '%s\n' "$$src" | sed 's/^/  /' | head -20; \
	 echo ""; \
	 echo "  Add an entry under ## [Unreleased] in this branch, so the"; \
	 echo "  release is a promotion rather than an archaeology exercise."; \
	 echo "  A purely internal change still gets one honest line."; \
	 exit 1
