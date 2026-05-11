cmake_minimum_required(VERSION 3.16)
project(my_project VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(ENABLE_SIMD "Enable SIMD flags (-march=native -ffast-math / /arch:AVX2 /fp:fast)" OFF)
if(ENABLE_SIMD)
    if(MSVC)
        add_compile_options(/arch:AVX2 /fp:fast)
    else()
        add_compile_options(-march=native -ffast-math)
    endif()
endif()

find_package(Python3 REQUIRED COMPONENTS Development NumPy)

set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/my_project")

# Combined C shared library — links all component OBJECT libraries.
# No Python dependency; distributable to C, C++, and Rust consumers.
add_library(my_project_lib SHARED native/src/my_project_lib.c)
set_target_properties(my_project_lib PROPERTIES OUTPUT_NAME my_project)
target_include_directories(my_project_lib PUBLIC
    $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/native/inc>
    $<INSTALL_INTERFACE:include>)

enable_testing()

# ── Components ───────────────────────────────────────────────────────────────
# just-makeit init appends: add_subdirectory + target_sources($<TARGET_OBJECTS:...>) per component

# ── Install ──────────────────────────────────────────────────────────────────

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(TARGETS my_project_lib
    EXPORT my_project-targets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

install(DIRECTORY ${CMAKE_SOURCE_DIR}/native/inc/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
    FILES_MATCHING PATTERN "*.h"
    PATTERN "pyex_common.h" EXCLUDE)

install(EXPORT my_project-targets
    FILE my_project-config.cmake
    NAMESPACE my_project::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/my_project)

write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/my_project-config-version.cmake"
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/my_project-config-version.cmake"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/my_project)

configure_file(cmake/my-project.pc.in my-project.pc @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/my-project.pc"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
add_subdirectory(native/src/engine)
target_sources(my_project_lib PRIVATE $<TARGET_OBJECTS:engine_core>)
