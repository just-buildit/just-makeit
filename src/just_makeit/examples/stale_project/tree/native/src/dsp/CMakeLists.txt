add_library(dsp_core OBJECT dsp_core.c energy.c)
target_include_directories(dsp_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc)

if(BUILD_PYTHON)
# dsp Python module — aggregates: Fir
Python3_add_library(dsp MODULE WITH_SOABI dsp_ext.c)
target_link_libraries(dsp PRIVATE
    dsp_core
    fir_core
    Python3::NumPy)
target_include_directories(dsp PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
set_target_properties(dsp PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/dsp"
    RUNTIME_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}/dsp")
add_custom_command(TARGET dsp POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<TARGET_FILE:dsp>"
        "${PYTHON_PACKAGE_DIR}/dsp/$<TARGET_FILE_NAME:dsp>"
    VERBATIM
    COMMENT "Copy dsp extension module")
endif()
