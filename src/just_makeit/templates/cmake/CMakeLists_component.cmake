<<component_core_decl>>
<<extra_include_dirs_on_core>><<extra_link_on_core>>
# The Python extension module, copied into the package after each build.
if(BUILD_PYTHON)
Python3_add_library(<<component>> MODULE WITH_SOABI <<component>>_ext.c<<extra_ext_sources>>)
target_link_libraries(<<component>> PRIVATE
    <<component>>_core
    <<extra_link_libs_block>>Python3::NumPy)
target_include_directories(<<component>> PRIVATE ${CMAKE_SOURCE_DIR}/<<inc_dir>>)
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

# The C unit test; `ctest` runs it.
add_executable(test_<<component>>_core
    ${CMAKE_SOURCE_DIR}/<<native_test_c>>
    ${CMAKE_SOURCE_DIR}/<<native_symbols_c>>)
target_link_libraries(test_<<component>>_core PRIVATE
    <<component>>_core
    <<test_link_libs_block>>${JM_MATH_LIBRARY})
target_include_directories(test_<<component>>_core
    PRIVATE ${CMAKE_SOURCE_DIR}/<<inc_dir>><<native_test_inc>>)
add_test(NAME test_<<component>>_core COMMAND test_<<component>>_core)

# The C benchmark; built, but not run by ctest.
add_executable(bench_<<component>>_core
    ${CMAKE_SOURCE_DIR}/<<native_bench_c>>)
target_link_libraries(bench_<<component>>_core PRIVATE
    <<component>>_core
    <<test_link_libs_block>>${JM_MATH_LIBRARY})
target_include_directories(bench_<<component>>_core
    PRIVATE ${CMAKE_SOURCE_DIR}/<<inc_dir>>
            ${CMAKE_SOURCE_DIR}/<<native_bench_inc>>)
