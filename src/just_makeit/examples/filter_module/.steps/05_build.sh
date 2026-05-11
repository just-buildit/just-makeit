cmake -B build -S . \
    -DCMAKE_BUILD_TYPE=Release \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
ctest --test-dir build --output-on-failure
pip install -e .
