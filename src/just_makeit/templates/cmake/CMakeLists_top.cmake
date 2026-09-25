cmake_minimum_required(VERSION 3.16)
# gh-1368: an unset build type is Release. It has to be decided BEFORE
# project(), which is where CMake fills in its own default -- and for an
# MSVC-like compiler (clang-cl) that default is Debug, which defines _DEBUG,
# which makes pyconfig.h link the debug interpreter's python3X_d.lib. That
# library ships only with a debug Python, so a bare `cmake -B build` did not
# link. (A multi-config generator ignores this; the runtime setting below is
# what makes its Debug link.) `make build` and `jm build` already pass Release;
# this makes a bare configure agree with them on every platform.
if(NOT DEFINED CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE
      Release
      CACHE STRING "Build type: Release, Debug, RelWithDebInfo, MinSizeRel")
endif()
project(
  <<project_underscore>>
  VERSION <<version>>
  DESCRIPTION "<<project>> C library"
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
# gh-1368: a Python extension always uses the RELEASE C runtime (/MD) under
# MSVC, in every configuration. The debug runtime (/MDd) makes the compiler
# predefine _DEBUG, and pyconfig.h answers _DEBUG by linking the debug
# interpreter's python3X_d.lib -- which only a debug Python ships, so a Debug
# build did not link (measured in Visual Studio 2026 with clang-cl). Debug
# keeps /Od and full debug info; only the debug CRT's heap checks go, and those
# need a debug Python anyway. Project-wide rather than per extension: the
# component cores are linked into the same DLL, and two CRTs in one DLL are two
# heaps, so a buffer allocated by one and freed by the other corrupts both. Set
# before any target exists, which is when CMake reads it.
if(MSVC AND BUILD_PYTHON)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()
if(BUILD_PYTHON)
  find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module
                                           NumPy)
endif()

set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/<<package>>")

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
  # gh-1452: libm is part of this library's LINK INTERFACE, not a private
  # detail. jm's headers DEFINE -- `step()` is `static inline` by default,
  # `JM_FORCEINLINE` under --perf -- so a consumer that calls it compiles the
  # body into its OWN object, and `ld` has not resolved a consumer's undefined
  # symbol through a dependency's NEEDED since binutils 2.22. PUBLIC puts it in
  # the exported target's INTERFACE_LINK_LIBRARIES.
  #
  # Two spellings on purpose, and neither is the bare name `m`. The build
  # interface is the resolved PATH, which no target named `m` can shadow
  # (gh-1305). The install interface is the linker FLAG `-lm`: a path from THIS
  # machine is wrong on the consumer's, and the bare name is worse than it
  # looks -- CMake resolves it against the PRODUCER's targets while writing the
  # export, so `jm module m` made it the Python module ("requires target m that
  # is not in any export set"). A `-`-prefixed item is never a target name.
  # Empty where there is no libm (Windows), so it guards itself.
  if(JM_MATH_LIBRARY)
    target_link_libraries(
      ${lib_target} PUBLIC $<BUILD_INTERFACE:${JM_MATH_LIBRARY}>
                           $<INSTALL_INTERFACE:-lm>)
  endif()
endforeach()
# gh-1368: one OUTPUT_NAME for both is unambiguous on Linux and macOS
# (lib<name>.so vs lib<name>.a) and a collision on Windows, where the SHARED
# library's import library and the STATIC library are both <name>.lib --
# `ninja: error: multiple rules generate <name>.lib`, before any C compiles.
# Renamed only where it has to be, as doppler's own CMake does.
if(WIN32)
  set_target_properties(<<project_underscore>>_lib_static
                        PROPERTIES OUTPUT_NAME <<project_underscore>>_static)
endif()
# gh-1368: a Windows DLL exports only what is marked __declspec(dllexport), and
# jm marks nothing -- so the shared library's import library was empty and a C
# consumer linking it failed on every symbol (`undefined symbol:
# <comp>_create`), found by the Windows artifact smoke. Exporting all is the
# DLL equivalent of an ELF shared library's default visibility. No effect
# elsewhere.
set_target_properties(<<project_underscore>>_lib
                      PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)

enable_testing()

# ── Components (add_subdirectory lines appended here by just-makeit)
# ──────────

# ── Modules (add_subdirectory lines appended here by just-makeit)
# ─────────────

# ── Install ──────────────────────────────────────────────────────────────────
# gh-1589: jm renders everything from the line above down to "End install" on
# every `apply`, so each packaging fix reaches this project. Put install rules
# of your own below that line, or in a file you include() from there.

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

# gh-1582: the ABI version. The shared library installs as lib<name>.so.X.Y.Z
# with the soname lib<name>.so.<ABI> and a lib<name>.so link, so a release that
# breaks the ABI installs BESIDE the old library instead of over it, and a
# program linked against the old one keeps loading it. Under 0.x every minor
# release may break (semver), so the ABI is major.minor there and major from
# 1.0 on -- the rule find_package's version file below applies too. macOS takes
# SOVERSION as the dylib's compatibility_version; a Windows DLL ignores it.
if(PROJECT_VERSION_MAJOR EQUAL 0)
  set(JM_ABI_VERSION ${PROJECT_VERSION_MAJOR}.${PROJECT_VERSION_MINOR})
  set(JM_VERSION_COMPATIBILITY SameMinorVersion)
else()
  set(JM_ABI_VERSION ${PROJECT_VERSION_MAJOR})
  set(JM_VERSION_COMPATIBILITY SameMajorVersion)
endif()
set_target_properties(
  <<project_underscore>>_lib PROPERTIES VERSION ${PROJECT_VERSION}
                                        SOVERSION ${JM_ABI_VERSION})

# gh-1594: on macOS the installed library names itself by its absolute path, as
# Homebrew's and MacPorts' do. CMake's default install name is
# @rpath/lib<name>.dylib, which only a program carrying an LC_RPATH can load:
# find_package consumers get one from CMake, but a program linked by
# pkg-config, a Makefile or Xcode does not, and dyld refuses to start it.
# $<INSTALL_PREFIX> is the prefix the install step uses, so `cmake --install
# --prefix B` is honoured as the .pc's prefix is; it needs CMake 3.17, and 3.16
# names the configured prefix. No effect off Apple platforms.
if(IS_ABSOLUTE "${CMAKE_INSTALL_LIBDIR}")
  set(JM_INSTALL_NAME_DIR "${CMAKE_INSTALL_LIBDIR}")
elseif(CMAKE_VERSION VERSION_LESS 3.17)
  set(JM_INSTALL_NAME_DIR "${CMAKE_INSTALL_FULL_LIBDIR}")
else()
  set(JM_INSTALL_NAME_DIR "$<INSTALL_PREFIX>/${CMAKE_INSTALL_LIBDIR}")
endif()
set_target_properties(<<project_underscore>>_lib
                      PROPERTIES INSTALL_NAME_DIR "${JM_INSTALL_NAME_DIR}")

install(
  TARGETS <<project_underscore>>_lib <<project_underscore>>_lib_static
  EXPORT <<project_underscore>>-targets
  # RUNTIME is where Windows puts a .dll (gh-1368): without it the DLL was
  # never installed, and a consumer linked against an import library whose
  # DLL was not there to load.
  RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
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

# gh-1582: SameMinorVersion under 0.x, where a minor release may break; see
# JM_ABI_VERSION above.
write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
  VERSION ${PROJECT_VERSION}
  COMPATIBILITY ${JM_VERSION_COMPATIBILITY})

install(
  FILES
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
  DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>)

# gh-1582: the build tree is a package too. The config and version files above
# are written into it already; exporting the targets beside them lets a sibling
# build use this one without installing it: `cmake
# -D<<project_underscore>>_DIR=<this build dir>`.
export(
  EXPORT <<project_underscore>>-targets
  FILE "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-targets.cmake"
  NAMESPACE <<project_underscore>>::)

# gh-1582: the .pc names where its files were ACTUALLY installed, as an
# absolute prefix, and that is decided at INSTALL time -- `cmake --install
# --prefix B` chooses the prefix then, and a configure-time prefix named one
# where nothing was installed. DESTDIR is not part of it: a staged tree names
# its real target, which is what a distribution package needs. Relocation is
# the consumer's side of pkg-config: a moved tree is read with `pkg-config
# --define-prefix`, a staged one with PKG_CONFIG_SYSROOT_DIR. An absolute
# prefix is also what lets pkg-config drop -I/-L for its system dirs under
# /usr, which it does only for a path spelled literally.
#
# A libdir or includedir given as an absolute path (GNUInstallDirs under Nix or
# Guix) is where the files are whatever the prefix, so it is written as itself,
# never as `${exec_prefix}//abs`; a relative one follows the prefix. gh-1452:
# libm is in the library's link interface (see its PUBLIC link above), so the
# .pc says so in `Libs:` rather than `Libs.private`: it is needed to link a
# CONSUMER, whose own object compiles jm's inline step().
if(JM_MATH_LIBRARY)
  set(JM_PC_LIBM " -lm")
else()
  set(JM_PC_LIBM "")
endif()
set(JM_PC_PREFIX "%JM_INSTALL_PREFIX%")
set(JM_PC_LIBDIR "\${exec_prefix}/${CMAKE_INSTALL_LIBDIR}")
if(IS_ABSOLUTE "${CMAKE_INSTALL_LIBDIR}")
  set(JM_PC_LIBDIR "${CMAKE_INSTALL_LIBDIR}")
endif()
set(JM_PC_INCLUDEDIR "\${prefix}/${CMAKE_INSTALL_INCLUDEDIR}")
if(IS_ABSOLUTE "${CMAKE_INSTALL_INCLUDEDIR}")
  set(JM_PC_INCLUDEDIR "${CMAKE_INSTALL_INCLUDEDIR}")
endif()
# gh-1582: the optional fields, each rendered only when it has something to
# say, so no `.pc` carries an empty `URL:` or a blank line for an absent one.
# The template puts this slot at the START of its `Version:` line and each
# field ends its own line, so an empty slot leaves nothing behind. (A slot on a
# line of its own cannot: configure_file ends every output line with a newline,
# so an empty one is a blank line.) Requires.private / Libs.private are the
# complete lines the external-deps block above sets.
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
configure_file(cmake/<<project>>.pc.in <<project>>.pc.configured @ONLY)
# Runs at install time, before the install(FILES) below copies its result:
# install rules run in the order they are declared, in one script, so the path
# set by the first rule is seen by the second. The bracket argument is not
# expanded here, so ${CMAKE_INSTALL_PREFIX} is the one the install step has.
# The configured path does not depend on the configuration, so a multi-config
# generator installs the same.
install(CODE "set(JM_PC_FILE \"${CMAKE_CURRENT_BINARY_DIR}/<<project>>.pc\")")
install(
  CODE [[
file(READ "${JM_PC_FILE}.configured" _jm_pc)
string(REPLACE "%JM_INSTALL_PREFIX%" "${CMAKE_INSTALL_PREFIX}" _jm_pc "${_jm_pc}")
file(WRITE "${JM_PC_FILE}" "${_jm_pc}")
]])
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/<<project>>.pc"
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
# ── End install ──────────────────────────────────────────────────────────────
