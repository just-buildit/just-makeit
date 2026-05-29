# OBJECT library — pure C core, no Python dependency.
# Linked into both the Python DSO and the combined libmy_dsp.so.
add_library(<<component>>_core OBJECT <<component>>_core.c)
target_include_directories(<<component>>_core PUBLIC
    ${CMAKE_SOURCE_DIR}/native/inc
    ${CMAKE_SOURCE_DIR}/native/inc/<<component>>)
<<extra_link_on_core>>
if(BUILD_PYTHON)
Python3_add_library(<<component>> MODULE WITH_SOABI <<component>>_ext.c)
target_link_libraries(<<component>> PRIVATE
    <<component>>_core
    <<extra_link_libs_block>>Python3::NumPy)
target_include_directories(<<component>> PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
if(WIN32 AND CMAKE_C_COMPILER_ID STREQUAL "GNU")
    # Avoid pulling in libgcc_s_seh-1.dll at runtime; libwinpthread-1.dll
    # is copied once at configure time by the top CMakeLists.
    target_link_options(<<component>> PRIVATE -static-libgcc)
endif()
set_target_properties(<<component>> PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}")
add_custom_command(TARGET <<component>> POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<TARGET_FILE:<<component>>>"
        "${PYTHON_PACKAGE_DIR}/$<TARGET_FILE_NAME:<<component>>>"
    VERBATIM
    COMMENT "Copy <<component>> extension module")
endif()

add_executable(test_<<component>>_core
    ${CMAKE_SOURCE_DIR}/native/tests/test_<<component>>_core.c)
target_link_libraries(test_<<component>>_core PRIVATE
    <<component>>_core
    <<extra_link_libs_block>>m)
target_include_directories(test_<<component>>_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_<<component>>_core COMMAND test_<<component>>_core)

add_executable(bench_<<component>>_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_<<component>>_core.c)
target_link_libraries(bench_<<component>>_core PRIVATE
    <<component>>_core
    <<extra_link_libs_block>>m)
target_include_directories(bench_<<component>>_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc
            ${CMAKE_SOURCE_DIR}/native/benchmarks)
