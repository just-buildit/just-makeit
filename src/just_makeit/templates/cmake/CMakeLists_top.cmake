cmake_minimum_required(VERSION 3.16)
# Default to a Release build when none is given. This must come before
# project(): with clang-cl, CMake's own default is Debug, which links against a
# debug Python library (python3X_d.lib) that a normal Python install lacks.
if(NOT DEFINED CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE
      Release
      CACHE STRING "Build type: Release, Debug, RelWithDebInfo, MinSizeRel")
endif()
project(
  <<project_underscore>>
  VERSION <<cmake_version>>
  DESCRIPTION "<<project_underscore>> C library"
  LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Optional: compile for this machine's CPU with fast floating point. Binaries
# built this way may not run on older CPUs.
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

# Windows builds use clang-cl (MSVC's cl.exe has no C99 _Complex).
if(WIN32)
  # Silence the Windows C runtime's warnings on standard C functions (fopen,
  # strncpy, strdup, ...), and make <math.h> define M_PI.
  add_compile_definitions(_CRT_SECURE_NO_WARNINGS _CRT_NONSTDC_NO_DEPRECATE
                          _USE_MATH_DEFINES)
endif()
if(MSVC AND CMAKE_C_COMPILER_ID STREQUAL "Clang")
  # Compute complex multiply and divide inline. Otherwise clang calls helper
  # functions (__mulsc3, __muldc3) that the MSVC linker cannot find.
  add_compile_options(/clang:-fcx-limited-range)
endif()

# Build the Python extension modules. Turn off (-DBUILD_PYTHON=OFF) to build
# only the C library.
option(BUILD_PYTHON "Build Python C extensions" ON)
# On Windows, link every target against the release C runtime, even in Debug
# builds: the debug runtime pulls in a debug Python library that a normal
# install lacks. Debug builds keep full debug info and no optimisation.
if(MSVC AND BUILD_PYTHON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()
if(BUILD_PYTHON)
  # If a Python interpreter was named (CMakePresets.json names the project's
  # .venv) but does not exist, stop with instructions instead of CMake's bare
  # "Could NOT find Python3". This happens when an IDE configures the folder
  # before the .venv is created. Paths are compared after resolving symlinks
  # and Windows short names, ignoring case on Windows.
  if(Python3_EXECUTABLE
     AND IS_ABSOLUTE "${Python3_EXECUTABLE}"
     AND NOT EXISTS "${Python3_EXECUTABLE}")
    file(TO_CMAKE_PATH "${Python3_EXECUTABLE}" _jm_py)
    set(_jm_own FALSE)
    string(FIND "${_jm_py}" "/.venv/" _jm_at REVERSE)
    if(NOT _jm_at EQUAL -1)
      string(SUBSTRING "${_jm_py}" 0 ${_jm_at} _jm_root)
      get_filename_component(_jm_root "${_jm_root}" REALPATH)
      get_filename_component(_jm_src "${CMAKE_SOURCE_DIR}" REALPATH)
      if(WIN32)
        string(TOLOWER "${_jm_root}" _jm_root)
        string(TOLOWER "${_jm_src}" _jm_src)
      endif()
      if(_jm_root STREQUAL _jm_src)
        set(_jm_own TRUE)
      endif()
    endif()
    if(NOT _jm_own)
      message(
        FATAL_ERROR
          "Python3_EXECUTABLE is ${Python3_EXECUTABLE}, which does not exist. "
          "Point it at an interpreter that does, where it was set: the "
          "configure preset or your CMakeUserPresets.json, or "
          "-DPython3_EXECUTABLE= on the cmake line (PYTHON= for make).")
    endif()
    if(WIN32)
      set(_jm_venv_help
          [=[  with pip:  py -m venv .venv
             .venv\Scripts\python -m pip install numpy
  with uv:   uv sync]=])
    else()
      set(_jm_venv_help
          [=[  with pip:  python3 -m venv .venv
             .venv/bin/python -m pip install numpy
  with uv:   uv sync]=])
    endif()
    message(
      FATAL_ERROR
        "Python3_EXECUTABLE is ${Python3_EXECUTABLE}, which does not exist. "
        "It is the project's own .venv: create that from the project "
        "folder, either way:\n${_jm_venv_help}\n"
        "then configure again (in Visual Studio: Project > Delete Cache and "
        "Reconfigure).")
  endif()
  # The interpreter, its headers for building extensions, and NumPy's headers.
  find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module
                                           NumPy)
endif()

# Built extension modules are copied here, into the Python package, so the
# package imports straight from the source tree.
set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/<<package>>")

# The project's C library, built both shared and static, with no Python
# dependency. Each component's compiled C is added to both below.
add_library(<<project_underscore>>_lib SHARED
            native/src/<<project_underscore>>_lib.c)
add_library(<<project_underscore>>_lib_static STATIC
            native/src/<<project_underscore>>_lib.c)
foreach(lib_target <<project_underscore>>_lib
                   <<project_underscore>>_lib_static)
  target_include_directories(
    ${lib_target} PUBLIC $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/<<inc_dir>>>
                         $<INSTALL_INTERFACE:include>)
  # Programs that use this library also link the math library (libm): the
  # headers contain inline functions that call it. In this build that is libm's
  # full path; once installed it is the flag -lm, since a path from this
  # machine would be wrong on another. Skipped where there is no separate libm
  # (Windows).
  if(JM_MATH_LIBRARY)
    target_link_libraries(
      ${lib_target} PUBLIC $<BUILD_INTERFACE:${JM_MATH_LIBRARY}>
                           $<INSTALL_INTERFACE:-lm>)
  endif()
endforeach()

# Lets `ctest` run the C tests each component registers with add_test().
enable_testing()

# ── Components (add_subdirectory lines appended here by just-makeit)
# ──────────

# ── Modules (add_subdirectory lines appended here by just-makeit)
# ─────────────

# ── Install ──────────────────────────────────────────────────────────────────
# How `cmake --install` installs the C library for other projects to use: the
# libraries, headers, a CMake package (find_package) and a pkg-config file.
# just-makeit rewrites this section, down to "End install", on every `apply`;
# put install rules of your own after that line.

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

# ABI version: which releases are binary compatible. The shared library is
# installed as lib<name>.so.X.Y.Z with a lib<name>.so.<ABI> link, so an
# incompatible release installs beside the old one, and programs built against
# the old one keep working. Before 1.0 any minor release may break
# compatibility (semver), so the ABI is X.Y; from 1.0 it is X.
if(PROJECT_VERSION_MAJOR EQUAL 0)
  set(JM_ABI_VERSION ${PROJECT_VERSION_MAJOR}.${PROJECT_VERSION_MINOR})
  set(JM_VERSION_COMPATIBILITY SameMinorVersion)
else()
  set(JM_ABI_VERSION ${PROJECT_VERSION_MAJOR})
  set(JM_VERSION_COMPATIBILITY SameMajorVersion)
endif()

# macOS: the installed library records its absolute install path, as Homebrew's
# do, so any program linked against it can find it at run time. (CMake's
# default, @rpath, works only for programs that set an rpath.) Uses the prefix
# given to `cmake --install --prefix` (CMake 3.17+).
if(IS_ABSOLUTE "${CMAKE_INSTALL_LIBDIR}")
  set(JM_INSTALL_NAME_DIR "${CMAKE_INSTALL_LIBDIR}")
elseif(CMAKE_VERSION VERSION_LESS 3.17)
  set(JM_INSTALL_NAME_DIR "${CMAKE_INSTALL_FULL_LIBDIR}")
else()
  set(JM_INSTALL_NAME_DIR "$<INSTALL_PREFIX>/${CMAKE_INSTALL_LIBDIR}")
endif()

# Linux and other ELF systems: the installed shared library searches its own
# directory ($ORIGIN) for the libraries it depends on, plus the directory of
# any dependency outside the system paths, so it loads even when installed to a
# non-standard prefix. Setting -DCMAKE_INSTALL_RPATH or
# -DCMAKE_INSTALL_RPATH_USE_LINK_PATH yourself overrides this.
if(NOT APPLE
   AND NOT WIN32
   AND NOT CYGWIN)
  if(DEFINED CMAKE_INSTALL_RPATH)
    set(JM_INSTALL_RPATH "${CMAKE_INSTALL_RPATH}")
  else()
    set(JM_INSTALL_RPATH "$ORIGIN")
  endif()
  if(DEFINED CMAKE_INSTALL_RPATH_USE_LINK_PATH)
    set(JM_INSTALL_RPATH_USE_LINK_PATH ${CMAKE_INSTALL_RPATH_USE_LINK_PATH})
  else()
    set(JM_INSTALL_RPATH_USE_LINK_PATH ON)
  endif()
endif()

# Every library this project installs, as `<target stem>:<exported name>`:
# lib<<project_underscore>> first, then each [project.libraries] entry. The
# packaging rules below loop over this list, so every library is installed the
# same way.
set(JM_LIBRARIES "<<project_underscore>>:<<project_underscore>>")
# ── Libraries: [project.libraries] (gh-1600) ─────────────────────────────────
# ── End libraries ────────────────────────────────────────────────────────────

# Link libraries and compile definitions that every user of this library needs,
# from [project] public_link_libs and public_defines. PUBLIC passes them on to
# anything that links the library, installed or not.
foreach(jm_lib <<project_underscore>>_lib <<project_underscore>>_lib_static)
  if(JM_PUBLIC_LINK_LIBS)
    target_link_libraries(${jm_lib} PUBLIC ${JM_PUBLIC_LINK_LIBS})
  endif()
  if(JM_PUBLIC_DEFINES)
    target_compile_definitions(${jm_lib} PUBLIC ${JM_PUBLIC_DEFINES})
  endif()
endforeach()

foreach(jm_row IN LISTS JM_LIBRARIES)
  string(REPLACE ":" ";" _jm_fields "${jm_row}")
  list(GET _jm_fields 0 _jm_lib)
  list(GET _jm_fields 1 _jm_export)
  # File name, version, and the target name other projects link after
  # find_package(): <<project_underscore>>::<<project_underscore>>, plus
  # -static for the static library; an additional library is
  # <<project_underscore>>::<name>. On Windows, export every function from the
  # DLL (as Linux does by default), and give the static library its own name so
  # its .lib does not clash with the DLL's.
  set_target_properties(
    ${_jm_lib}_lib
    PROPERTIES OUTPUT_NAME ${_jm_lib}
               VERSION ${PROJECT_VERSION}
               SOVERSION ${JM_ABI_VERSION}
               INSTALL_NAME_DIR "${JM_INSTALL_NAME_DIR}"
               EXPORT_NAME ${_jm_export}
               WINDOWS_EXPORT_ALL_SYMBOLS ON)
  if(DEFINED JM_INSTALL_RPATH_USE_LINK_PATH)
    set_target_properties(
      ${_jm_lib}_lib
      PROPERTIES INSTALL_RPATH "${JM_INSTALL_RPATH}"
                 INSTALL_RPATH_USE_LINK_PATH ${JM_INSTALL_RPATH_USE_LINK_PATH})
  endif()
  set_target_properties(
    ${_jm_lib}_lib_static PROPERTIES OUTPUT_NAME ${_jm_lib}
                                     EXPORT_NAME ${_jm_export}-static)
  if(WIN32)
    set_target_properties(${_jm_lib}_lib_static PROPERTIES OUTPUT_NAME
                                                           ${_jm_lib}_static)
  endif()
  install(
    TARGETS ${_jm_lib}_lib ${_jm_lib}_lib_static
    EXPORT <<project_underscore>>-targets
    # Two install components, as Linux packages split them: `runtime` is the
    # shared library (a Windows .dll goes to bin/); `dev` is everything needed
    # to build against it. `cmake --install --component runtime` installs one;
    # a plain `cmake --install` installs both.
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR} COMPONENT runtime
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
            COMPONENT runtime
            NAMELINK_COMPONENT dev
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR} COMPONENT dev)
endforeach()

# The public headers. pyex_common.h is for the Python extensions only.
install(
  DIRECTORY ${CMAKE_SOURCE_DIR}/<<inc_dir>>/
  DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
  COMPONENT dev
  FILES_MATCHING
  PATTERN "*.h"
  PATTERN "pyex_common.h" EXCLUDE)

# The CMake package that find_package(<<project_underscore>>) reads: the
# exported targets, a config file, and a version file.
install(
  EXPORT <<project_underscore>>-targets
  FILE <<project_underscore>>-targets.cmake
  NAMESPACE <<project_underscore>>::
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>
  COMPONENT dev)

configure_package_config_file(
  cmake/<<project_underscore>>-config.cmake.in
  "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config.cmake"
  INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>)

# Which versions satisfy a find_package version request; the same rule as the
# ABI version above.
write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
  VERSION ${PROJECT_VERSION}
  COMPATIBILITY ${JM_VERSION_COMPATIBILITY})

install(
  FILES
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>
  COMPONENT dev)

# Lets another project use this build without installing it: cmake
# -D<<project_underscore>>_DIR=<this build dir>
export(
  EXPORT <<project_underscore>>-targets
  FILE "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-targets.cmake"
  NAMESPACE <<project_underscore>>::)

# The pkg-config file (lib<name>.pc), for builds that use pkg-config rather
# than CMake. Its prefix is filled in at install time, so it names wherever
# `cmake --install --prefix` actually put the files. libm and the public link
# libraries go on its `Libs:` line, because programs using this library need
# them too (see the PUBLIC link above).
if(JM_MATH_LIBRARY)
  set(JM_PC_LIBM " -lm")
else()
  set(JM_PC_LIBM "")
endif()
foreach(jm_flag IN LISTS JM_PUBLIC_LINK_LIBS)
  string(APPEND JM_PC_LIBM " ${jm_flag}")
endforeach()
foreach(jm_def IN LISTS JM_PUBLIC_DEFINES)
  string(APPEND JM_PC_CFLAGS " -D${jm_def}")
endforeach()
set(JM_PC_PREFIX "%JM_INSTALL_PREFIX%")
set(JM_PC_LIBDIR "\${exec_prefix}/${CMAKE_INSTALL_LIBDIR}")
if(IS_ABSOLUTE "${CMAKE_INSTALL_LIBDIR}")
  set(JM_PC_LIBDIR "${CMAKE_INSTALL_LIBDIR}")
endif()
set(JM_PC_INCLUDEDIR "\${prefix}/${CMAKE_INSTALL_INCLUDEDIR}")
if(IS_ABSOLUTE "${CMAKE_INSTALL_INCLUDEDIR}")
  set(JM_PC_INCLUDEDIR "${CMAKE_INSTALL_INCLUDEDIR}")
endif()
# Optional .pc fields, each written only when it has a value: the project URL,
# and the Requires.private / Libs.private lines for external dependencies (set
# in the external-deps block).
set(JM_PC_EXTRA_FIELDS "")
if(NOT PROJECT_HOMEPAGE_URL STREQUAL "")
  string(APPEND JM_PC_EXTRA_FIELDS "URL: ${PROJECT_HOMEPAGE_URL}\n")
endif()
if(NOT "${JM_PC_REQUIRES_PRIVATE}" STREQUAL "")
  string(APPEND JM_PC_EXTRA_FIELDS "${JM_PC_REQUIRES_PRIVATE}\n")
endif()
if(NOT "${JM_PC_LIBS_PRIVATE}" STREQUAL "")
  string(APPEND JM_PC_EXTRA_FIELDS "${JM_PC_LIBS_PRIVATE}\n")
endif()
# One .pc per library, from cmake/<<project_underscore>>.pc.in. An additional
# library's .pc requires lib<<project_underscore>>'s, which carries the
# dependencies and flags.
foreach(jm_row IN LISTS JM_LIBRARIES)
  string(REPLACE ":" ";" _jm_fields "${jm_row}")
  list(GET _jm_fields 0 _jm_lib)
  set(JM_PC_NAME ${_jm_lib})
  if(_jm_lib STREQUAL "<<project_underscore>>")
    set(JM_PC_DESCRIPTION "${PROJECT_DESCRIPTION}")
    set(JM_PC_ROW_FIELDS "${JM_PC_EXTRA_FIELDS}")
    set(JM_PC_ROW_LIBS "${JM_PC_LIBM}")
    set(JM_PC_ROW_CFLAGS "${JM_PC_CFLAGS}")
  else()
    string(TOUPPER "${_jm_lib}" _jm_upper)
    set(JM_PC_DESCRIPTION "${JM_LIBRARY_${_jm_upper}_DESCRIPTION}")
    set(JM_PC_ROW_FIELDS "Requires: <<project_underscore>>\n")
    set(JM_PC_ROW_LIBS "")
    set(JM_PC_ROW_CFLAGS "")
  endif()
  configure_file(cmake/<<project_underscore>>.pc.in ${_jm_lib}.pc.configured
                 @ONLY)
  # At install time, write the real install prefix into the .pc, then install
  # it. The old file is removed first, in case an earlier `sudo cmake
  # --install` left it owned by root.
  install(CODE "set(JM_PC_FILE \"${CMAKE_CURRENT_BINARY_DIR}/${_jm_lib}.pc\")"
          COMPONENT dev)
  install(
    CODE [[
file(READ "${JM_PC_FILE}.configured" _jm_pc)
string(REPLACE "%JM_INSTALL_PREFIX%" "${CMAKE_INSTALL_PREFIX}" _jm_pc "${_jm_pc}")
file(REMOVE "${JM_PC_FILE}")
file(WRITE "${JM_PC_FILE}" "${_jm_pc}")
]]
    COMPONENT dev)
  install(
    FILES "${CMAKE_CURRENT_BINARY_DIR}/${_jm_lib}.pc"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig
    COMPONENT dev)
endforeach()
# ── End install ──────────────────────────────────────────────────────────────
