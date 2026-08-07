# <<project>> project Makefile
#
# Targets:
#   make              Configure + build (Release)
#   make test         CTest + <<py_test_label>>
#   make bench        C + Python benchmarks; dated snapshot in benchmarks/history/
#   make just-build   PEP 517 hook for just-buildit
#   make clean        Remove build artifacts
#   make help         Show this message

ifeq ($(OS), Windows_NT)
SHELL      = cmd.exe
NPROC      ?= 4
PYTHON     ?= $(or $(JUST_BUILDIT_PYTHON),$(shell python -c "import sys,pathlib;print(pathlib.Path(sys.executable).as_posix())"))
else
SHELL      = /bin/sh
NPROC      ?= $(shell nproc 2>/dev/null || echo 4)
PYTHON     ?= $(or $(JUST_BUILDIT_PYTHON),$(shell python3 -c "import sys,pathlib;print(pathlib.Path(sys.executable).as_posix())" 2>/dev/null),$(shell python -c "import sys,pathlib;print(pathlib.Path(sys.executable).as_posix())" 2>/dev/null))
endif
BUILD_DIR  ?= build

# An empty interpreter is never a valid answer here (gh-814). Every cmake
# invocation below passes -DPython3_EXECUTABLE=$(PYTHON), and CMake treats an
# empty value as "discover one yourself" rather than as an error — so if the
# fallbacks above found nothing, fail HERE, where the cause is visible, rather
# than at import time with an ABI mismatch.
ifeq ($(strip $(PYTHON)),)
$(error no Python interpreter found — set PYTHON=/path/to/python (gh-814))
endif
BUILD_TYPE ?= Release

# cmake needs numpy's HEADERS, not an importable numpy (gh-824).
# `find_package(Python3 ... NumPy)` resolves Python3_NumPy_INCLUDE_DIRS from
# `numpy.get_include()`, so a numpy whose package imports while that directory
# is absent satisfies an `import numpy` guard and then fails inside cmake —
# as `Could NOT find Python3_NumPy_INCLUDE_DIRS`, or, when the path is only
# read at generate time, as `Imported target "Python3::NumPy" includes
# non-existent path`. Both come from this one condition.
#
# The install is a REinstall for the same reason: in that state numpy is
# already present, so a plain `pip install numpy` is a no-op and cannot repair
# it. And the verify runs unconditionally afterwards, because an environment
# where the reinstall did not help should say so here — naming the directory —
# rather than at the cmake error two steps later. Same trade gh-814 made for
# an unresolvable interpreter.
NUMPY_HEADERS_OK     = import numpy, os, sys; sys.exit(0 if os.path.isdir(numpy.get_include()) else 1)
NUMPY_HEADERS_VERIFY = import numpy, os, sys; p = numpy.get_include(); sys.exit(0 if os.path.isdir(p) else 'error: numpy is installed but its C headers are missing from ' + p + ' -- cmake cannot build the extension without them. Reinstall numpy for this interpreter (gh-824).')

# On Windows (OS=Windows_NT is always set by the OS itself, regardless of
# shell), force the MinGW Makefiles generator so CMake uses gcc instead of
# MSVC.  MSVC does not support C99 float complex; gcc does.
ifeq ($(OS), Windows_NT)
CMAKE_GENERATOR ?= MinGW Makefiles
CMAKE_GEN_FLAG  := -G "$(CMAKE_GENERATOR)"
else
CMAKE_GEN_FLAG  :=
endif

.PHONY: all build test bench just-build docs clean help

all: build

$(BUILD_DIR)/CMakeCache.txt:
ifeq ($(OS), Windows_NT)
	$(PYTHON) -c "$(NUMPY_HEADERS_OK)" 2>nul || $(PYTHON) -m pip install --force-reinstall numpy
	$(PYTHON) -c "$(NUMPY_HEADERS_VERIFY)"
<<ensure_pytest_win>>
else
	@$(PYTHON) -c "$(NUMPY_HEADERS_OK)" 2>/dev/null || $(PYTHON) -m pip install --force-reinstall numpy
	@$(PYTHON) -c "$(NUMPY_HEADERS_VERIFY)"
<<ensure_pytest_unix>>
endif
	cmake -B $(BUILD_DIR) -S . \
		$(CMAKE_GEN_FLAG) \
		-DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \
		-DPython3_EXECUTABLE=$(PYTHON) \
		-DCMAKE_EXPORT_COMPILE_COMMANDS=ON

compile_commands.json: $(BUILD_DIR)/CMakeCache.txt
	cp $(BUILD_DIR)/compile_commands.json $@

build: $(BUILD_DIR)/CMakeCache.txt
	cmake --build $(BUILD_DIR) --parallel $(NPROC)

test: build
	ctest --test-dir $(BUILD_DIR) --output-on-failure
ifeq ($(OS), Windows_NT)
	<<py_test_cmd_win>>
else
	<<py_test_cmd_unix>>
endif

bench:
	just-makeit bench

coverage:
	cmake -B $(BUILD_DIR)/cov -S . $(CMAKE_GEN_FLAG) \
		-DCMAKE_BUILD_TYPE=Debug \
		-DCMAKE_C_FLAGS="--coverage -O0" \
		-DPython3_EXECUTABLE=$(PYTHON)
	cmake --build $(BUILD_DIR)/cov --parallel $(NPROC)
	ctest --test-dir $(BUILD_DIR)/cov --output-on-failure
	lcov --capture --directory $(BUILD_DIR)/cov \
		--output-file $(BUILD_DIR)/cov/coverage.info \
		--ignore-errors inconsistent 2>/dev/null || \
	  lcov --capture --directory $(BUILD_DIR)/cov \
		--output-file $(BUILD_DIR)/cov/coverage.info
	lcov --remove $(BUILD_DIR)/cov/coverage.info '/usr/*' '*/tests/*' \
		--output-file $(BUILD_DIR)/cov/coverage_filtered.info \
		--ignore-errors unused
	mkdir -p docs/coverage/c
	genhtml $(BUILD_DIR)/cov/coverage_filtered.info \
		--output-directory docs/coverage/c
	@echo "C coverage: docs/coverage/c/index.html"
	$(PYTHON) -m pytest src/ \
		--cov=<<package>> \
		--cov-report=html:docs/coverage/python \
		--cov-report=term-missing
	@echo "Python coverage: docs/coverage/python/index.html"

# $(PYTHON), NOT $(JUST_BUILDIT_PYTHON) — gh-814. Do not "simplify" this back
# to the raw variable: it is set by just-buildit and EMPTY otherwise, and an
# empty `-DPython3_EXECUTABLE=` does not mean "use the default", it means
# CMake picks the interpreter itself. On any box with more than one numpy (a
# system python3-numpy plus a venv one — which jb.toml actively creates) the
# extension can then compile against one numpy and be imported under the
# other. That surfaces as `compiled using NumPy 1.x cannot be run in NumPy
# 2.x` at IMPORT time, arbitrarily far from the build that caused it.
# $(PYTHON) already prefers $(JUST_BUILDIT_PYTHON) and falls back to a real
# interpreter, so it is correct in both cases and empty in neither.
just-build:
	cmake -B $(BUILD_DIR) -S . \
		$(CMAKE_GEN_FLAG) \
		-DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \
		-DPython3_EXECUTABLE=$(PYTHON) \
		-DCMAKE_EXPORT_COMPILE_COMMANDS=ON
	cmake --build $(BUILD_DIR) --parallel $(NPROC)
	mkdir -p $(JUST_BUILDIT_OUTPUT_DIR)
	cp -r src/<<package>> $(JUST_BUILDIT_OUTPUT_DIR)/<<package>>

docs: build
	@command -v doxygen >/dev/null 2>&1 || \
	  { echo "doxygen not found — install it first"; exit 1; }
	doxygen Doxyfile
	@echo "C API docs: docs/doxygen/html/index.html"
	@command -v zensical >/dev/null 2>&1 || \
	  { echo "zensical not found — uv add --dev zensical mkdocstrings-python"; exit 1; }
	zensical build
	@echo "Python API docs: site/index.html"

clean:
	rm -rf $(BUILD_DIR) site docs/coverage docs/doxygen
	find src -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true

help:
	@echo ""
	@echo "<<project>> build targets"
	@echo ""
	@echo "  make               Configure + build"
	@echo "  make test          Run CTest + <<py_test_label>>"
	@echo "  make bench         Run C + Python benchmarks"
	@echo "  make coverage      C (lcov) + Python (pytest-cov) coverage reports"
	@echo "  make docs          Doxygen (C) + Zensical (Python) API docs"
	@echo "  make clean         Remove build artifacts"
	@echo ""
