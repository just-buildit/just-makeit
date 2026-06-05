<<module_core_lib_block>>if(BUILD_PYTHON)
# <<module_comment>>
Python3_add_library(<<module>> MODULE WITH_SOABI <<module>>_ext.c<<extra_ext_sources>>)
target_link_libraries(<<module>> PRIVATE
    <<object_core_libs>>
    <<extra_link_libs_block>>Python3::NumPy)
target_include_directories(<<module>> PRIVATE ${CMAKE_SOURCE_DIR}/native/inc<<extra_include_dirs_block>>)
if(WIN32 AND CMAKE_C_COMPILER_ID STREQUAL "GNU")
    target_link_options(<<module>> PRIVATE -static-libgcc)
    get_filename_component(gcc_bin "${CMAKE_C_COMPILER}" DIRECTORY)
    foreach(dll_name IN ITEMS libwinpthread-1.dll)
        if(EXISTS "${gcc_bin}/${dll_name}")
            add_custom_command(TARGET <<module>> POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${gcc_bin}/${dll_name}"
                    "${PYTHON_PACKAGE_DIR}/<<module>>"
                VERBATIM
                COMMENT "Copy Windows runtime DLL ${dll_name}")
        endif()
    endforeach()
endif()
set_target_properties(<<module>> PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/<<module>>"
    RUNTIME_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/<<module>>")
add_custom_command(TARGET <<module>> POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<TARGET_FILE:<<module>>>"
        "${PYTHON_PACKAGE_DIR}/<<module>>/$<TARGET_FILE_NAME:<<module>>>"
    VERBATIM
    COMMENT "Copy <<module>> extension module")
endif()
