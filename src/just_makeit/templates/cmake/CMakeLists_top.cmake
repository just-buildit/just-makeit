cmake_minimum_required(VERSION 3.16)
# gh-1368: an unset build type is Release. It has to be decided BEFORE
# project(), which is where CMake fills in its own default -- and for an
# MSVC-like compiler (clang-cl) that default is Debug, which defines _DEBUG,
# which makes pyconfig.h link the debug interpreter's python3X_d.lib. That
# library ships only with a debug Python, so a bare `cmake -B build` did not
# link. `make build` and `jm build` already pass Release; this makes a bare
# configure agree with them on every platform.
if(NOT DEFINED CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE
      Release
      CACHE STRING "Build type: Release, Debug, RelWithDebInfo, MinSizeRel")
endif()
project(
  <<project_underscore>>
  VERSION <<version>>
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

# gh-1368: the MSVC ABI, as clang-cl builds it (cl.exe has no _Complex).
if(WIN32)
  # The CRT deprecates portable C99 (fopen, strncpy, localtime -> the _s forms;
  # strdup -> _strdup) on every use, burying the real diagnostics.
  # _USE_MATH_DEFINES exposes M_PI, which <math.h> hides there by default.
  add_compile_definitions(_CRT_SECURE_NO_WARNINGS _CRT_NONSTDC_NO_DEPRECATE
                          _USE_MATH_DEFINES)
endif()
if(MSVC AND CMAKE_C_COMPILER_ID STREQUAL "Clang")
  # Without a complex-range relaxation clang lowers `_Complex` multiply and
  # divide to compiler-rt calls (__mulsc3, __muldc3, ...) for C99 Annex G's
  # inf/NaN corner cases, and the MSVC link has nothing that defines them:
  # `undefined symbol: __mulsc3` after every object compiles. This is the
  # narrowest flag that inlines them; ENABLE_SIMD's /fp:fast implies it.
  # Spelled /clang: because clang-cl ignores a bare GCC-style flag, and guarded
  # on the compiler ID because cl.exe rejects /clang: outright. doppler
  # measured the same wall on the same toolchain.
  add_compile_options(/clang:-fcx-limited-range)
endif()

option(BUILD_PYTHON "Build Python C extensions" ON)
if(BUILD_PYTHON)
  find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module
                                           NumPy)
endif()

set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/<<package>>")

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
add_library(<<project_underscore>>_lib SHARED
            native/src/<<project_underscore>>_lib.c)
add_library(<<project_underscore>>_lib_static STATIC
            native/src/<<project_underscore>>_lib.c)
foreach(lib_target <<project_underscore>>_lib
                   <<project_underscore>>_lib_static)
  target_include_directories(
    ${lib_target} PUBLIC $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/native/inc>
                         $<INSTALL_INTERFACE:include>)
  set_target_properties(${lib_target} PROPERTIES OUTPUT_NAME
                                                 <<project_underscore>>)
endforeach()

enable_testing()

# ── Components (add_subdirectory lines appended here by just-makeit)
# ──────────

# ── Modules (add_subdirectory lines appended here by just-makeit)
# ─────────────

# ── Install ──────────────────────────────────────────────────────────────────

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(
  TARGETS <<project_underscore>>_lib <<project_underscore>>_lib_static
  EXPORT <<project_underscore>>-targets
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

install(
  DIRECTORY ${CMAKE_SOURCE_DIR}/native/inc/
  DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
  FILES_MATCHING
  PATTERN "*.h"
  PATTERN "pyex_common.h" EXCLUDE)

install(
  EXPORT <<project_underscore>>-targets
  FILE <<project_underscore>>-targets.cmake
  NAMESPACE <<project_underscore>>::
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>)

configure_package_config_file(
  cmake/<<project_underscore>>-config.cmake.in
  "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config.cmake"
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>)

write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
  VERSION ${PROJECT_VERSION}
  COMPATIBILITY SameMajorVersion)

install(
  FILES
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>)

configure_file(cmake/<<project>>.pc.in <<project>>.pc @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/<<project>>.pc"
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
