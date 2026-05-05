# gain project Makefile
#
# Targets:
#   make             Configure + build (Release)
#   make test        CTest + pytest
#   make just-build  PEP 517 hook for just-buildit
#   make clean       Remove build artifacts
#   make help        Show this message

SHELL      = /bin/sh
BUILD_DIR  ?= build
BUILD_TYPE ?= Release
NPROC      ?= $(shell nproc 2>/dev/null || echo 4)
PYTHON     ?= $(or $(JUST_BUILDIT_PYTHON),$(shell which python3))

.PHONY: all build test just-build clean help

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
	$(PYTHON) -m pytest src/ -v 2>/dev/null || \
		$(PYTHON) -m unittest discover -s src/gain/tests -t src -p "test_*.py" -v

just-build: build
	mkdir -p $(JUST_BUILDIT_OUTPUT_DIR)
	cp -r src/gain $(JUST_BUILDIT_OUTPUT_DIR)/gain

clean:
	rm -rf $(BUILD_DIR)
	find src -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true

help:
	@echo ""
	@echo "gain build targets"
	@echo ""
	@echo "  make          Configure + build"
	@echo "  make test     Run CTest + pytest"
	@echo "  make clean    Remove build artifacts"
	@echo ""
