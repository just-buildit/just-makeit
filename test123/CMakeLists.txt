cmake_minimum_required(VERSION 3.16)
project(test123 VERSION 0.1.0 LANGUAGES C)

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

find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module NumPy)

set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/test123")

# Combined C library — shared + static, no Python dependency.
# Component OBJECT libraries are wired in via target_sources below.
add_library(test123_lib SHARED native/src/test123_lib.c)
add_library(test123_lib_static STATIC native/src/test123_lib.c)
foreach(_t test123_lib test123_lib_static)
    target_include_directories(${_t} PUBLIC
        $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/native/inc>
        $<INSTALL_INTERFACE:include>)
    set_target_properties(${_t} PROPERTIES OUTPUT_NAME test123)
endforeach()

enable_testing()

# ── Components (add_subdirectory lines appended here by just-makeit) ──────────

# ── Modules (add_subdirectory lines appended here by just-makeit) ─────────────

# ── Install ──────────────────────────────────────────────────────────────────

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(TARGETS test123_lib test123_lib_static
    EXPORT test123-targets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

install(DIRECTORY ${CMAKE_SOURCE_DIR}/native/inc/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
    FILES_MATCHING PATTERN "*.h"
    PATTERN "pyex_common.h" EXCLUDE)

install(EXPORT test123-targets
    FILE test123-config.cmake
    NAMESPACE test123::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/test123)

write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/test123-config-version.cmake"
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/test123-config-version.cmake"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/test123)

configure_file(cmake/test123.pc.in test123.pc @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/test123.pc"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
