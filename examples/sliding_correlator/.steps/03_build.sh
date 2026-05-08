cmake -B build -S . -DCMAKE_BUILD_TYPE=Debug \
    -DPython3_EXECUTABLE=$(python3 -c "import sys; print(sys.executable)")
cmake --build build --parallel
cmake --build build --target test ARGS="--output-on-failure"
pip install -e .
