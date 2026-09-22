# OBJECT library — pure C core, no Python dependency.
# Linked into both the Python DSO and the combined libmy_dsp.so.
add_library(gain_core OBJECT gain_core.c)
target_include_directories(gain_core PUBLIC
    ${CMAKE_SOURCE_DIR}/native/inc
    ${CMAKE_SOURCE_DIR}/native/inc/gain)

if(BUILD_PYTHON)
Python3_add_library(gain MODULE WITH_SOABI gain_ext.c)
target_link_libraries(gain PRIVATE
    gain_core
    Python3::NumPy)
target_include_directories(gain PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
set_target_properties(gain PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}")
add_custom_command(TARGET gain POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<TARGET_FILE:gain>"
        "${PYTHON_PACKAGE_DIR}/$<TARGET_FILE_NAME:gain>"
    VERBATIM
    COMMENT "Copy gain extension module")
endif()

add_executable(test_gain_core
    ${CMAKE_SOURCE_DIR}/native/tests/test_gain_core.c)
target_link_libraries(test_gain_core PRIVATE
    gain_core
    m)
target_include_directories(test_gain_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_gain_core COMMAND test_gain_core)

add_executable(bench_gain_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_gain_core.c)
target_link_libraries(bench_gain_core PRIVATE
    gain_core
    m)
target_include_directories(bench_gain_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc
            ${CMAKE_SOURCE_DIR}/native/benchmarks)
