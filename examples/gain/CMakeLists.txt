cmake_minimum_required(VERSION 3.16)
project(gain VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python3 REQUIRED COMPONENTS Development NumPy)

# ── Core C library (static) ──────────────────────────────────────────────────
add_library(gain_core STATIC
    native/src/gain/gain_core.c
)
target_include_directories(gain_core PUBLIC
    native/inc
)

# ── Python extension ─────────────────────────────────────────────────────────
Python3_add_library(gain MODULE WITH_SOABI
    native/src/gain/gain_ext.c
)
target_link_libraries(gain PRIVATE
    gain_core
    Python3::NumPy
)
target_include_directories(gain PRIVATE native/inc)
set_target_properties(gain PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/src/gain")

# ── CTest ────────────────────────────────────────────────────────────────────
enable_testing()

add_executable(test_gain_core
    native/tests/test_gain_core.c
)
target_link_libraries(test_gain_core PRIVATE gain_core)
target_include_directories(test_gain_core PRIVATE native/inc)

add_test(NAME test_gain_core COMMAND test_gain_core)
