cmake -B build -S . -DCMAKE_BUILD_TYPE=Release \
    -DPython3_EXECUTABLE="$(command -v python3)"
cmake --build build --parallel 4
