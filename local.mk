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
LOCAL_TARGETS = examples-clean

examples-clean: ## Remove build artifacts from every example
	@for d in examples/*/; do \
	    [ -f "$$d/Makefile" ] && $(MAKE) -C "$$d" clean 2>/dev/null || true; \
	done
	find examples -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true
