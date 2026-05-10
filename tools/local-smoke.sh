#!/usr/bin/env bash
# local-smoke.sh — end-to-end artifact smoke test in a clean Docker container.
#
# Usage:
#   scripts/local-smoke.sh              # uses version from pyproject.toml
#   scripts/local-smoke.sh 0.9.0        # specific published version
#   scripts/local-smoke.sh local        # installs from local wheel (builds first)
#
# Requires: docker, sudo

set -euo pipefail

# ── Version resolution ────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODE="pypi"
VERSION="${1:-}"

if [[ "${VERSION}" == "local" ]]; then
    MODE="local"
    VERSION=""
elif [[ -z "${VERSION}" ]]; then
    VERSION="$(grep '^version' "$ROOT/pyproject.toml" | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
fi

echo "════════════════════════════════════════════════════════════════"
if [[ "$MODE" == "local" ]]; then
    echo "  just-makeit local smoke test  (local wheel)"
else
    echo "  just-makeit local smoke test  v${VERSION}"
fi
echo "  image: python:3.12-slim-trixie"
echo "════════════════════════════════════════════════════════════════"

# ── Local wheel build (if needed) ────────────────────────────────────────────

DOCKER_EXTRA_ARGS=()

if [[ "$MODE" == "local" ]]; then
    echo "→ Building local wheel..."
    cd "$ROOT"
    pip install just-buildit --quiet 2>/dev/null || true
    python -m build --wheel --no-isolation -q 2>/dev/null || \
        uv build --wheel --no-build-isolation -q
    WHEEL_FILE="$(ls -t "$ROOT"/dist/just_makeit-*.whl | head -1)"
    echo "  wheel: $WHEEL_FILE"
    DOCKER_EXTRA_ARGS+=(-v "${WHEEL_FILE}:/just_makeit.whl")
fi

# ── Container script ──────────────────────────────────────────────────────────

sudo docker run --rm \
    "${DOCKER_EXTRA_ARGS[@]}" \
    python:3.12-slim-trixie bash -s -- "$MODE" "$VERSION" <<'CONTAINER'
set -euo pipefail
MODE="$1"
VERSION="$2"

step() { echo; echo "── $* ──────────────────────────────────────────"; }

# ── System deps ───────────────────────────────────────────────────────────────

step "Installing system deps"
apt-get update -qq
apt-get install -y -qq cmake gcc pkg-config > /dev/null

# ── Install just-makeit ───────────────────────────────────────────────────────

step "Installing just-makeit"
if [[ "$MODE" == "local" ]]; then
    pip install /just_makeit.whl --quiet
else
    for i in $(seq 1 20); do
        pip install "just-makeit==${VERSION}" --no-cache-dir --quiet && break
        echo "  not on PyPI yet, retry ${i}/20..."
        sleep 30
    done
    pip show just-makeit | grep -q "Version: ${VERSION}"
fi
just-makeit --version 2>/dev/null || just-makeit help | head -1
echo "  just-makeit installed ok"

# ═════════════════════════════════════════════════════════════════════════════
# PATH 1 — Standalone object workflow (fir_filter + gain)
# ═════════════════════════════════════════════════════════════════════════════

step "PATH 1: Scaffold fir_filter (standalone, array state, --object)"
just-makeit new my_fir \
    --object fir_filter \
    --state "coeffs:float[16]" \
    --state "delay:float _Complex[16]" \
    --state "gain:float:1.0"

step "Implement fir_filter_step"
python3 - <<'PYEOF'
import pathlib, re
header = pathlib.Path("my_fir/native/inc/fir_filter/fir_filter_core.h")
impl = (
    "fir_filter_step(fir_filter_state_t *state, float complex x)\n"
    "{\n"
    "    memmove(&state->delay[1], &state->delay[0], 15 * sizeof(float complex));\n"
    "    state->delay[0] = x;\n"
    "    float complex y = 0.0f + 0.0f * I;\n"
    "    for (int k = 0; k < 16; k++)\n"
    "        y += state->coeffs[k] * state->delay[k];\n"
    "    return (float complex)state->gain * y;\n"
    "}"
)
stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float complex\s*\n"
    r"fir_filter_step\(const fir_filter_state_t \*state.*?\n\}",
    re.DOTALL,
)
text = header.read_text()
m = stub_re.search(text)
assert m, "stub not found"
header.write_text(stub_re.sub(m.group(1) + " float complex\n" + impl, text))
print("  patched fir_filter_step")
PYEOF

step "Enable perf annotations"
just-makeit perf --project my_fir 2>/dev/null || \
    (cd my_fir && just-makeit perf)

step "Build and test fir_filter"
(cd my_fir && make && make test)

step "Add second standalone object (gain) — verify __init__.py splice"
(cd my_fir && just-makeit object gain \
    --arg-type float \
    --return-type float \
    --state gain:float:1.0)
grep "from .gain import Gain" my_fir/src/my_fir/__init__.py
grep '"Gain"' my_fir/src/my_fir/__init__.py
(cd my_fir && make && make test)
echo "  __init__.py splice: ok"

# ── C library distribution (fir_filter) ──────────────────────────────────────

step "Install fir_filter C library"
cmake -S my_fir -B my_fir/build -DCMAKE_INSTALL_PREFIX="$HOME/fir_prefix" -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -3
cmake --install my_fir/build

step "Verify C consumers — pkg-config"
PREFIX="$HOME/fir_prefix"
LIB_DIR=$(find "$PREFIX" -name "libmy_fir.*" -exec dirname {} \; | head -1)
export PKG_CONFIG_PATH
PKG_CONFIG_PATH=$(find "$PREFIX" -name "my-fir.pc" -exec dirname {} \; | head -1)

cat > /tmp/fir_consumer.c << 'CEOF'
#include "my_fir.h"
#include <assert.h>
#include <complex.h>
#include <math.h>
#include <stdio.h>
int main(void) {
    fir_filter_state_t *f = fir_filter_create(1.0f);
    float h[16] = {0}; h[0] = 0.25f; h[1] = 0.50f; h[2] = 0.25f;
    fir_filter_set_coeffs(f, h);
    float complex in[16] = {0}, out[16] = {0};
    in[0] = 1.0f;
    fir_filter_steps(f, in, out, 16);
    assert(fabsf(crealf(out[0]) - 0.25f) < 1e-6f);
    assert(fabsf(crealf(out[1]) - 0.50f) < 1e-6f);
    assert(fabsf(crealf(out[2]) - 0.25f) < 1e-6f);
    assert(fabsf(crealf(out[3]))          < 1e-6f);
    fir_filter_destroy(f);
    puts("pkg-config consumer: ok");
    return 0;
}
CEOF

gcc -O2 -std=c99 \
    $(pkg-config --cflags my-fir) \
    /tmp/fir_consumer.c \
    $(pkg-config --libs my-fir) \
    -Wl,-rpath,"$LIB_DIR" -lm \
    -o /tmp/fir_consumer_pkgconfig
/tmp/fir_consumer_pkgconfig

step "Verify C consumers — CMake find_package"
mkdir /tmp/cmake_fir_consumer
cp /tmp/fir_consumer.c /tmp/cmake_fir_consumer/
cat > /tmp/cmake_fir_consumer/CMakeLists.txt << 'CEOF'
cmake_minimum_required(VERSION 3.16)
project(fir_consumer C)
find_package(my_fir REQUIRED)
add_executable(fir_consumer fir_consumer.c)
target_link_libraries(fir_consumer PRIVATE my_fir::my_fir_lib m)
set_target_properties(fir_consumer PROPERTIES INSTALL_RPATH_USE_LINK_PATH ON)
CEOF
cmake -B /tmp/cmake_fir_consumer/build /tmp/cmake_fir_consumer \
    -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DCMAKE_BUILD_RPATH="$LIB_DIR" 2>&1 | tail -3
cmake --build /tmp/cmake_fir_consumer/build
/tmp/cmake_fir_consumer/build/fir_consumer

echo "  C library consumers: ok"

# ═════════════════════════════════════════════════════════════════════════════
# PATH 2 — Module / object workflow (filter: Fir + Biquad + Iir)
# ═════════════════════════════════════════════════════════════════════════════

step "PATH 2: Scaffold filter_module project"
mkdir filter_module_test && cd filter_module_test
just-makeit new my_filters
cd my_filters
just-makeit module filter
just-makeit object fir \
    --module filter \
    --state "coeffs:float[16]" \
    --state "delay:float _Complex[16]" \
    --state "gain:float:1.0"
just-makeit object biquad \
    --module filter \
    --arg-type float \
    --return-type float \
    --state "b0:double:1.0" \
    --state "b1:double:0.0" \
    --state "b2:double:0.0" \
    --state "a1:double:0.0" \
    --state "a2:double:0.0" \
    --state "w1:double:0.0" \
    --state "w2:double:0.0"

step "Verify generated structure"
grep "FirObject"    native/src/filter/filter_ext.c
grep "BiquadObject" native/src/filter/filter_ext.c
grep "PyInit_filter" native/src/filter/filter_ext.c
grep "Fir"   src/my_filters/filter/__init__.py
grep "Biquad" src/my_filters/filter/__init__.py
grep "fir_core"    native/src/filter/CMakeLists.txt
grep "biquad_core" native/src/filter/CMakeLists.txt
# OBJECT-only CMakeLists — no Python3_add_library
! grep -q "Python3_add_library" native/src/fir/CMakeLists.txt
! grep -q "Python3_add_library" native/src/biquad/CMakeLists.txt
echo "  generated structure: ok"

step "Patch fir_step and biquad_step stubs"
python3 - <<'PYEOF'
import pathlib, re

def patch(path, fn, return_type, new_sig, body):
    p = pathlib.Path(path)
    text = p.read_text()
    stub_re = re.compile(
        rf"(static inline|JM_FORCEINLINE JM_HOT) {re.escape(return_type)}\s*\n"
        rf"{re.escape(fn)}\(const .*?\n\}}",
        re.DOTALL,
    )
    m = stub_re.search(text)
    assert m, f"stub not found in {path}"
    p.write_text(stub_re.sub(m.group(1) + f" {return_type}\n" + new_sig + body, text))
    print(f"  patched {path}")

patch(
    "native/inc/fir/fir_core.h",
    "fir_step", "float complex",
    "fir_step(fir_state_t *state, float complex x)\n",
    "{\n"
    "    memmove(&state->delay[1], &state->delay[0], 15 * sizeof(float complex));\n"
    "    state->delay[0] = x;\n"
    "    float complex y = 0.0f;\n"
    "    for (int k = 0; k < 16; k++)\n"
    "        y += state->coeffs[k] * state->delay[k];\n"
    "    return (float complex)state->gain * y;\n"
    "}",
)
patch(
    "native/inc/biquad/biquad_core.h",
    "biquad_step", "float",
    "biquad_step(biquad_state_t *state, float x)\n",
    "{\n"
    "    double y  = state->b0 * (double)x + state->w1;\n"
    "    state->w1 = state->b1 * (double)x - state->a1 * y + state->w2;\n"
    "    state->w2 = state->b2 * (double)x - state->a2 * y;\n"
    "    return (float)y;\n"
    "}",
)
PYEOF

step "Build and test filter_module"
make && make test

step "Smoke test — Fir and Biquad importable and numerically correct"
python3 - <<'PYEOF'
import sys, math
sys.path.insert(0, 'src')
import numpy as np
from my_filters.filter import Fir, Biquad

# Fir: 3-tap box impulse response
fir = Fir(gain=1.0)
h = np.zeros(16, dtype=np.float32)
h[0] = h[1] = h[2] = 1.0 / 3
fir.set_coeffs(h)
imp = np.zeros(16, dtype=np.complex64); imp[0] = 1.0
ir  = fir.steps(imp)
assert abs(ir[0].real - 1/3) < 1e-5, f"ir[0]={ir[0].real}"
assert abs(ir[1].real - 1/3) < 1e-5, f"ir[1]={ir[1].real}"
assert abs(ir[2].real - 1/3) < 1e-5, f"ir[2]={ir[2].real}"
assert abs(ir[3].real)       < 1e-5, f"ir[3]={ir[3].real}"

# Biquad: passthrough
bq = Biquad(b0=1.0)
x  = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
y  = bq.steps(x)
assert all(abs(float(y[i]) - float(x[i])) < 1e-5 for i in range(4))

# Biquad: low-pass selectivity
fc, Q = 0.1, 0.707
w0 = 2 * math.pi * fc; alpha = math.sin(w0) / (2 * Q); c = math.cos(w0); a0 = 1 + alpha
bq2 = Biquad(b0=(1-c)/2/a0, b1=(1-c)/a0, b2=(1-c)/2/a0, a1=-2*c/a0, a2=(1-alpha)/a0)
t   = np.arange(512, dtype=np.float32) / 512
p_lo = float(np.mean(bq2.steps(np.cos(2*math.pi*0.05*t))**2)); bq2.reset()
p_hi = float(np.mean(bq2.steps(np.cos(2*math.pi*0.40*t))**2))
assert p_lo > 0.3,  f"passband too attenuated: {p_lo}"
assert p_hi < 0.01, f"stopband not rejected: {p_hi}"

print("  Fir + Biquad: ok")
PYEOF

step "Add third object (Iir) — verify module regeneration"
just-makeit object iir \
    --module filter \
    --arg-type float \
    --return-type float \
    --state "gain:float:1.0"
grep "IirObject"    native/src/filter/filter_ext.c
grep "FirObject"    native/src/filter/filter_ext.c
grep "BiquadObject" native/src/filter/filter_ext.c
grep "iir_core"     native/src/filter/CMakeLists.txt
grep "Iir"          src/my_filters/filter/__init__.py
echo "  module regeneration: ok"

# ── C library distribution (filter_module) ───────────────────────────────────

step "Install filter_module C library"
cmake -S . -B build -DCMAKE_INSTALL_PREFIX="$HOME/filters_prefix" -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -3
cmake --install build

step "Verify filter_module C consumers"
PREFIX="$HOME/filters_prefix"
LIB_DIR=$(find "$PREFIX" -name "libmy_filters.*" -exec dirname {} \; | head -1)
export PKG_CONFIG_PATH
PKG_CONFIG_PATH=$(find "$PREFIX" -name "my-filters.pc" -exec dirname {} \; | head -1)

cat > /tmp/filters_consumer.c << 'CEOF'
#include "my_filters.h"
#include <assert.h>
#include <complex.h>
#include <math.h>
#include <stdio.h>
int main(void) {
    fir_state_t *f = fir_create(1.0f);
    float h[16] = {0}; h[0] = 0.25f; h[1] = 0.50f; h[2] = 0.25f;
    fir_set_coeffs(f, h);
    float complex in[16] = {0}, out[16] = {0};
    in[0] = 1.0f;
    fir_steps(f, in, out, 16);
    assert(fabsf(crealf(out[0]) - 0.25f) < 1e-6f);
    assert(fabsf(crealf(out[1]) - 0.50f) < 1e-6f);
    assert(fabsf(crealf(out[2]) - 0.25f) < 1e-6f);
    assert(fabsf(crealf(out[3]))          < 1e-6f);
    fir_destroy(f);
    puts("pkg-config consumer: ok");
    return 0;
}
CEOF

gcc -O2 -std=c99 \
    $(pkg-config --cflags my-filters) \
    /tmp/filters_consumer.c \
    $(pkg-config --libs my-filters) \
    -Wl,-rpath,"$LIB_DIR" -lm \
    -o /tmp/filters_consumer_pkgconfig
/tmp/filters_consumer_pkgconfig

mkdir /tmp/cmake_filters_consumer
cp /tmp/filters_consumer.c /tmp/cmake_filters_consumer/
cat > /tmp/cmake_filters_consumer/CMakeLists.txt << 'CEOF'
cmake_minimum_required(VERSION 3.16)
project(filters_consumer C)
find_package(my_filters REQUIRED)
add_executable(filters_consumer filters_consumer.c)
target_link_libraries(filters_consumer PRIVATE my_filters::my_filters_lib m)
set_target_properties(filters_consumer PROPERTIES INSTALL_RPATH_USE_LINK_PATH ON)
CEOF
cmake -B /tmp/cmake_filters_consumer/build /tmp/cmake_filters_consumer \
    -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DCMAKE_BUILD_RPATH="$LIB_DIR" 2>&1 | tail -3
cmake --build /tmp/cmake_filters_consumer/build
/tmp/cmake_filters_consumer/build/filters_consumer

echo "  filter_module C library consumers: ok"

# ─────────────────────────────────────────────────────────────────────────────

echo
echo "════════════════════════════════════════════════════════════════"
echo "  ALL SMOKE TESTS PASSED"
echo "════════════════════════════════════════════════════════════════"
CONTAINER
