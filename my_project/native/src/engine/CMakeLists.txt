# OBJECT library — pure C core, no Python dependency.
# Linked into both the Python DSO and the combined libmy_dsp.so.
add_library(engine_core OBJECT engine_core.c)
target_include_directories(engine_core PUBLIC
    ${CMAKE_SOURCE_DIR}/native/inc
    ${CMAKE_SOURCE_DIR}/native/inc/engine)

Python3_add_library(engine MODULE WITH_SOABI engine_ext.c)
target_link_libraries(engine PRIVATE
    engine_core
    Python3::NumPy)
target_include_directories(engine PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
set_target_properties(engine PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}")

add_executable(test_engine_core
    ${CMAKE_SOURCE_DIR}/native/tests/test_engine_core.c)
target_link_libraries(test_engine_core PRIVATE engine_core m)
target_include_directories(test_engine_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_engine_core COMMAND test_engine_core)

add_executable(bench_engine_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_engine_core.c)
target_link_libraries(bench_engine_core PRIVATE engine_core m)
target_include_directories(bench_engine_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
