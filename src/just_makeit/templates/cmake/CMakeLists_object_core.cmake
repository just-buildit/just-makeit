<<object_core_decl>>
<<extra_include_dirs_on_object_core>><<extra_link_on_object_core>>
# The C unit test; `ctest` runs it.
add_executable(test_<<component>>_core
               ${CMAKE_SOURCE_DIR}/<<native_test_c>>
               ${CMAKE_SOURCE_DIR}/<<native_symbols_c>>)
target_link_libraries(test_<<component>>_core
                      PRIVATE <<component>>_core <<extra_link_libs_block>>${JM_MATH_LIBRARY})
target_include_directories(test_<<component>>_core
                           PRIVATE ${CMAKE_SOURCE_DIR}/<<inc_dir>><<native_test_inc_object>>)
add_test(NAME test_<<component>>_core COMMAND test_<<component>>_core)

# The C benchmark; built, but not run by ctest.
add_executable(
  bench_<<component>>_core
  ${CMAKE_SOURCE_DIR}/<<native_bench_c>>)
target_link_libraries(bench_<<component>>_core
                      PRIVATE <<component>>_core <<extra_link_libs_block>>${JM_MATH_LIBRARY})
target_include_directories(
  bench_<<component>>_core PRIVATE ${CMAKE_SOURCE_DIR}/<<inc_dir>>
                                   ${CMAKE_SOURCE_DIR}/<<native_bench_inc>>)
