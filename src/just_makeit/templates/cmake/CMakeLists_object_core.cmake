# OBJECT library — pure C core, no Python dependency.
add_library(<<component>>_core OBJECT <<component>>_core.c)
target_include_directories(
  <<component>>_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                            ${CMAKE_SOURCE_DIR}/native/inc/<<component>>)
<<extra_include_dirs_on_object_core>><<extra_link_on_object_core>>
add_executable(test_<<component>>_core
               ${CMAKE_SOURCE_DIR}/native/tests/test_<<component>>_core.c)
target_link_libraries(test_<<component>>_core
                      PRIVATE <<component>>_core <<extra_link_libs_block>>m)
target_include_directories(test_<<component>>_core
                           PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_<<component>>_core COMMAND test_<<component>>_core)

add_executable(
  bench_<<component>>_core
  ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_<<component>>_core.c)
target_link_libraries(bench_<<component>>_core
                      PRIVATE <<component>>_core <<extra_link_libs_block>>m)
target_include_directories(
  bench_<<component>>_core PRIVATE ${CMAKE_SOURCE_DIR}/native/inc
                                   ${CMAKE_SOURCE_DIR}/native/benchmarks)
