<<module_core_lib_block>>if(BUILD_PYTHON)
# <<module_comment>>
Python3_add_library(<<module>> MODULE WITH_SOABI <<module>>_ext.c<<extra_ext_sources>>)
target_link_libraries(<<module>> PRIVATE
    <<object_core_libs>>
    <<extra_link_libs_block>>Python3::NumPy)
target_include_directories(<<module>> PRIVATE ${CMAKE_SOURCE_DIR}/native/inc<<extra_include_dirs_block>>)
<<win_cmake_module>>set_target_properties(<<module>> PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/<<module>>"
    RUNTIME_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/<<module>>")
add_custom_command(TARGET <<module>> POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<TARGET_FILE:<<module>>>"
        "${PYTHON_PACKAGE_DIR}/<<module>>/$<TARGET_FILE_NAME:<<module>>>"
    VERBATIM
    COMMENT "Copy <<module>> extension module")
endif()
