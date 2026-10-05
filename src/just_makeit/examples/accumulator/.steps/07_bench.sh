PY=$(python3 -c "import sys; print(sys.executable)")
STEPS="$(python3 -c 'import just_makeit, pathlib; print(pathlib.Path(just_makeit.__file__).parent)')/examples/accumulator/.steps"

# Stage 1 — Release build, no SIMD flags (scalar reduction)
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DPython3_EXECUTABLE="$PY" \
    -DCMAKE_VERBOSE_MAKEFILE=OFF -Wno-dev
cmake --build build --parallel
echo "=== baseline (scalar) ==="
python3 bench.py

# Stage 2 — ENABLE_SIMD=ON: adds -march=native -ffast-math
#           -ffast-math allows the compiler to reassociate the reduction
#           and auto-vectorise steps() using AVX2 / AVX-512 lanes.
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON \
    -DPython3_EXECUTABLE="$PY" -Wno-dev
cmake --build build --parallel
echo "=== ENABLE_SIMD=ON (auto-vectorised) ==="
python3 bench.py

# Stage 3 — Explicit SIMD: replace steps() with JM_ADD_F32 + JM_HSUM_F32,
#           rebuild with ENABLE_SIMD=ON still active.
python3 "$STEPS/07_patch_perf.py"
cmake --build build --parallel
echo "=== explicit SIMD (JM_ADD_F32 + JM_HSUM_F32) ==="
python3 bench.py
