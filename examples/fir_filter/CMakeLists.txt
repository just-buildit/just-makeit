cmake_minimum_required(VERSION 3.16)
project(fir_filter VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python3 REQUIRED COMPONENTS Development NumPy)

set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/fir_filter")

enable_testing()

# ── Components ───────────────────────────────────────────────────────────────
# Added by: just-makeit init <component>
add_subdirectory(native/src/fir_filter)
