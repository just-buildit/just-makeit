cmake_minimum_required(VERSION 3.16)
project(
  stale
  VERSION 0.1.0
  LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(ENABLE_SIMD
       "Enable SIMD flags (-march=native -ffast-math / /arch:AVX2 /fp:fast)"
       OFF)
if(ENABLE_SIMD)
  if(MSVC)
    add_compile_options(/arch:AVX2 /fp:fast)
  else()
    add_compile_options(-march=native -ffast-math)
  endif()
endif()

option(BUILD_PYTHON "Build Python C extensions" ON)
if(BUILD_PYTHON)
  find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module
                                           NumPy)
endif()

set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/stale")

# On Windows/MinGW, libwinpthread-1.dll has to sit next to the .pyd files so
# Python can load them. Copy it once at configure time — per-target POST_BUILD
# copies race on parallel builds when multiple standalone objects share
# PYTHON_PACKAGE_DIR. (This single configure-time block is a harmless no-op off
# Windows; the per-component opt-in lives in [project] platforms — gh-213.)
if(WIN32
   AND CMAKE_C_COMPILER_ID STREQUAL "GNU"
   AND BUILD_PYTHON)
  get_filename_component(gcc_bin "${CMAKE_C_COMPILER}" DIRECTORY)
  if(EXISTS "${gcc_bin}/libwinpthread-1.dll")
    file(COPY "${gcc_bin}/libwinpthread-1.dll"
         DESTINATION "${PYTHON_PACKAGE_DIR}")
  endif()
endif()

# Combined C library — shared + static, no Python dependency. Component OBJECT
# libraries are wired in via target_sources below.
add_library(stale_lib SHARED
            native/src/stale_lib.c)
add_library(stale_lib_static STATIC
            native/src/stale_lib.c)
foreach(lib_target stale_lib
                   stale_lib_static)
  target_include_directories(
    ${lib_target} PUBLIC $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/native/inc>
                         $<INSTALL_INTERFACE:include>)
  set_target_properties(${lib_target} PROPERTIES OUTPUT_NAME
                                                 stale)
endforeach()

enable_testing()

# ── Components (add_subdirectory lines appended here by just-makeit)
add_subdirectory(native/src/fir)
target_sources(stale_lib PRIVATE $<TARGET_OBJECTS:fir_core>)
target_sources(stale_lib_static PRIVATE $<TARGET_OBJECTS:fir_core>)
add_subdirectory(native/src/gain)
target_sources(stale_lib PRIVATE $<TARGET_OBJECTS:gain_core>)
# ──────────

# ── Modules (add_subdirectory lines appended here by just-makeit)
add_subdirectory(native/src/dsp)
# ─────────────

# ── Install ──────────────────────────────────────────────────────────────────

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(
  TARGETS stale_lib stale_lib_static
  EXPORT stale-targets
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

install(
  DIRECTORY ${CMAKE_SOURCE_DIR}/native/inc/
  DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
  FILES_MATCHING
  PATTERN "*.h"
  PATTERN "pyex_common.h" EXCLUDE)

install(
  EXPORT stale-targets
  FILE stale-targets.cmake
  NAMESPACE stale::
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/stale)

configure_package_config_file(
  cmake/stale-config.cmake.in
  "${CMAKE_CURRENT_BINARY_DIR}/stale-config.cmake"
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/stale)

write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/stale-config-version.cmake"
  VERSION ${PROJECT_VERSION}
  COMPATIBILITY SameMajorVersion)

install(
  FILES
    "${CMAKE_CURRENT_BINARY_DIR}/stale-config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/stale-config-version.cmake"
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/stale)

configure_file(cmake/stale.pc.in stale.pc @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/stale.pc"
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
