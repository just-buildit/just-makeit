# my-project project Makefile
#
# Targets:
#   make              Configure + build (Release)
#   make test         CTest + pytest
#   make bench        C + Python benchmarks (output only)
#   make bench-save   Save benchmark baseline (tagged with git describe)
#   make bench-compare  Compare against last saved baseline
#   make just-build   PEP 517 hook for just-buildit
#   make clean        Remove build artifacts
#   make help         Show this message

SHELL      = /bin/sh
BUILD_DIR  ?= build
BUILD_TYPE ?= Release
NPROC      ?= $(shell nproc 2>/dev/null || echo 4)
PYTHON     ?= $(or $(JUST_BUILDIT_PYTHON),$(shell which python3))
BENCH_TAG  ?= $(shell git describe --tags --dirty 2>/dev/null || date +%Y%m%d)

.PHONY: all build test bench bench-save bench-compare just-build clean help

all: build

$(BUILD_DIR)/CMakeCache.txt:
	@$(PYTHON) -c "import numpy" 2>/dev/null || $(PYTHON) -m pip install numpy
	cmake -B $(BUILD_DIR) -S . \
		-DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \
		-DPython3_EXECUTABLE=$(PYTHON) \
		-DCMAKE_EXPORT_COMPILE_COMMANDS=ON

compile_commands.json: $(BUILD_DIR)/CMakeCache.txt
	cp $(BUILD_DIR)/compile_commands.json $@

build: $(BUILD_DIR)/CMakeCache.txt
	cmake --build $(BUILD_DIR) --parallel $(NPROC)

test: build
	ctest --test-dir $(BUILD_DIR) --output-on-failure
	$(PYTHON) -m pytest src/ -v 2>/dev/null; ret=$$?; 		[ $$ret -eq 0 ] || [ $$ret -eq 5 ] || 		$(PYTHON) -m unittest discover -s src/my_project/tests -t src -p "test_*.py" -v

bench: build
	@for b in $(BUILD_DIR)/bench_*_core; do [ -x "$$b" ] && echo "--- $$b ---" && "$$b" && echo; done
	$(PYTHON) -m pytest src/my_project/benchmarks/ -v --benchmark-disable-gc \
		2>/dev/null || echo "(hint: pip install pytest-benchmark)"

bench-save: build
	$(PYTHON) -m pytest src/my_project/benchmarks/ \
		--benchmark-save=$(BENCH_TAG) --benchmark-disable-gc

bench-compare: build
	$(PYTHON) -m pytest src/my_project/benchmarks/ \
		--benchmark-compare --benchmark-disable-gc

just-build: build
	mkdir -p $(JUST_BUILDIT_OUTPUT_DIR)
	cp -r src/my_project $(JUST_BUILDIT_OUTPUT_DIR)/my_project

clean:
	rm -rf $(BUILD_DIR)
	find src -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true

help:
	@echo ""
	@echo "my-project build targets"
	@echo ""
	@echo "  make               Configure + build"
	@echo "  make test          Run CTest + pytest"
	@echo "  make bench         Run C + Python benchmarks"
	@echo "  make bench-save    Save baseline (git describe tag)"
	@echo "  make bench-compare Compare against last saved baseline"
	@echo "  make clean         Remove build artifacts"
	@echo ""
