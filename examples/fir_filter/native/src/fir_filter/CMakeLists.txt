add_library(fir_filter_core STATIC fir_filter_core.c)
target_include_directories(fir_filter_core PUBLIC
    ${CMAKE_SOURCE_DIR}/native/inc)

Python3_add_library(fir_filter MODULE WITH_SOABI fir_filter_ext.c)
target_link_libraries(fir_filter PRIVATE
    fir_filter_core
    Python3::NumPy)
target_include_directories(fir_filter PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
set_target_properties(fir_filter PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}")

add_executable(test_fir_filter_core
    ${CMAKE_SOURCE_DIR}/native/tests/test_fir_filter_core.c)
target_link_libraries(test_fir_filter_core PRIVATE fir_filter_core)
target_include_directories(test_fir_filter_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_fir_filter_core COMMAND test_fir_filter_core)
