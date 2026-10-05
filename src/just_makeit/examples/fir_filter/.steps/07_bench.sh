# Baseline build (no SIMD). ENABLE_SIMD is a cached option, so say OFF
# explicitly: a configure that omits it keeps the last run's ON.
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=OFF \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
pip install -e . --force-reinstall
python3 bench.py

# Rebuild with SIMD
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DENABLE_SIMD=ON \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
pip install -e . --force-reinstall
python3 bench.py
