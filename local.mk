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
LOCAL_TARGETS = start-here examples-clean pr-watch install-deps-dev tool-install

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

# `install-deps` (standard) installs jb.toml's system packages with no group
# and no source selector, which is why ci.yml called `jbx` directly for the dev
# group and picked apt/brew in YAML. This ADDS a sibling rather than
# redefining the standard target — local.mk may only add, and make would warn
# about the override anyway.
#
# The source follows the OS, chosen here rather than in a workflow `if:`, so
# the choice lives with the command it qualifies.
INSTALL_DEPS_GROUP  ?= dev
INSTALL_DEPS_SOURCE ?= $(if $(filter Darwin,$(shell uname -s)),brew,apt)

install-deps-dev: ## Install the dev-group system packages (jb.toml)
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
