# Baseline build (no SIMD)
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release \
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
