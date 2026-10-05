#!/usr/bin/env bash
# gh-1590: install jm packages the documented way and consume them by the
# OFFICIAL pkg-config and CMake instructions -- the acceptance test for epic
# gh-1584. It reads top to bottom as what a user would type.
#
#   alpha   a jm package
#   beta    depends on alpha through [project] find_packages (pkg_config)
#   gamma   depends on alpha through [project] pkg_modules, version-bounded
#   delta   installs more than one library ([project.libraries], gh-1600)
#
# Each dependent's public header includes alpha's, and each is consumed by
# `pkg-config [--static] --cflags --libs` and by `find_package` with the
# shared and static targets -- every program built and RUN (consume()).
#
# CONSUMER_SMOKE_DEFAULT_PREFIX=1 (CI only): install to CMake's DEFAULT
#   prefix (/usr/local), with sudo when it is not writable, and consume with
#   NO hints at all -- no PKG_CONFIG_PATH, no CMAKE_PREFIX_PATH. That is the
#   standard layout, and the only honest test of it is a throwaway machine.
# Otherwise (a developer's box, `make gates`, and CI's second step): install
#   to PREFIX, default a fresh temp dir, and add exactly the hints the official
#   docs prescribe for a non-default prefix -- the two SEARCH paths, printed,
#   and the documented rpath on a pkg-config program's link line. Never a
#   loader path: that is what hid gh-1869. Nothing outside WORK and PREFIX is
#   touched.
#
# Either way it also installs one package into a SECOND prefix with its
# dependency left in the first, and runs a program that calls a library which
# alone calls another: the two layouts a program's own rpath cannot reach.
#
# CONSUMER_CMAKE_VERSION=3.16.3 runs everything with Kitware's release binary
#   of that version: the floor every generated project declares.

set -euo pipefail

say() { printf '\n==> %s\n' "$*"; }
die() {
    printf '::error::%s\n' "$*" >&2
    exit 1
}

# ── the jm under test ────────────────────────────────────────────────────────
# gh-1625: JM is the command line of THIS checkout's jm, and the Makefile is
# the one place that names it (`make consumer-smoke`). It used to default to
# whatever `just-makeit` came first on PATH -- on a developer's box a stale
# `uv tool` install, fifteen releases old -- so this gate could pass while
# saying nothing about the branch, or fail on the harness. There is no
# default now, and the version jm reports must be the one this tree
# declares, or nothing runs.
[[ -n ${JM:-} ]] \
    || die "JM is unset: run \`make consumer-smoke\`, which names this checkout's jm"
# A command line, not a path: the Makefile's is `uv run ... just-makeit`.
read -r -a JM_ARGV <<<"$JM"
tree_jm() { "${JM_ARGV[@]}" "$@"; }
TREE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
want=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$TREE/pyproject.toml")
[[ -n $want ]] || die "no [project] version in $TREE/pyproject.toml"
got=$(tree_jm --version) || die "\`$JM --version\` failed"
say "jm $got: $JM ($(command -v "${JM_ARGV[0]}" || echo "${JM_ARGV[0]} not found"))"
[[ $got == "$want" ]] \
    || die "the jm under test is $got but this tree is $want: \`$JM\` is not this checkout's jm"

WORK=${WORK:-$(mktemp -d)}
mkdir -p "$WORK"
CC=${CC:-cc}

# ── the toolchain ────────────────────────────────────────────────────────────
if [[ -n ${CONSUMER_CMAKE_VERSION:-} ]]; then
    [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] \
        || die "CONSUMER_CMAKE_VERSION needs Linux x86_64 (Kitware's binary)"
    v=$CONSUMER_CMAKE_VERSION
    say "CMake $v from Kitware's release"
    curl -fsSL --retry 3 \
        "https://github.com/Kitware/CMake/releases/download/v$v/cmake-$v-Linux-x86_64.tar.gz" \
        | tar -xz -C "$WORK"
    PATH="$WORK/cmake-$v-Linux-x86_64/bin:$PATH"
    [[ $(cmake --version | head -1) == "cmake version $v" ]] \
        || die "wanted CMake $v, got: $(cmake --version | head -1)"
fi
say "$(cmake --version | head -1); pkg-config $(pkg-config --version)"

# A hint already in the environment would make this pass for the wrong
# reason: the point is what works WITHOUT one. The loader path too (gh-1869):
# GNU ld reads LD_LIBRARY_PATH at LINK time as well, to find a library's own
# dependencies, so an inherited one hid a link failure, not only a load one.
unset PKG_CONFIG_PATH CMAKE_PREFIX_PATH LD_LIBRARY_PATH DYLD_LIBRARY_PATH \
    DYLD_FALLBACK_LIBRARY_PATH

SUDO=
if [[ ${CONSUMER_SMOKE_DEFAULT_PREFIX:-} == 1 ]]; then
    [[ -z ${PREFIX:-} ]] || die "PREFIX and CONSUMER_SMOKE_DEFAULT_PREFIX conflict"
    INSTALL_PREFIX=/usr/local # CMake's default on every Unix
    [[ -w $INSTALL_PREFIX ]] || SUDO=sudo
    CONFIGURE_PREFIX=()
else
    PREFIX=${PREFIX:-$WORK/prefix}
    CONFIGURE_PREFIX=("-DCMAKE_INSTALL_PREFIX=$PREFIX")
    # The official instructions for a non-default prefix: pkg-config's guide
    # (PKG_CONFIG_PATH) and cmake-packages(7) (CMAKE_PREFIX_PATH). lib64 too:
    # GNUInstallDirs picks it on Fedora-family hosts. NO loader path
    # (gh-1869): a pkg-config program finds its direct dependencies through
    # the rpath the docs prescribe (pc_rpath below), a find_package program
    # through the build rpath CMake gives it, and every library finds its own
    # dependencies through the RUNPATH jm installs it with.
    export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib64/pkgconfig"
    export CMAKE_PREFIX_PATH="$PREFIX"
    say "non-default prefix $PREFIX: PKG_CONFIG_PATH, CMAKE_PREFIX_PATH set"
fi

# The documented build and install of a CMake project.
install_project() {
    # ${a[@]+"${a[@]}"}: macOS's bash 3.2 calls an EMPTY array unbound under
    # `set -u`, which is exactly the default-prefix case.
    cmake -S "$1" -B "$1/build" -DBUILD_PYTHON=OFF \
        ${CONFIGURE_PREFIX[@]+"${CONFIGURE_PREFIX[@]}"}
    cmake --build "$1/build"
    $SUDO cmake --install "$1/build"
    # A new shared library under /usr/local/lib is found once the loader's
    # cache knows it (ldconfig(8)); macOS's dyld searches /usr/local/lib itself.
    if [[ -z ${PREFIX:-} && $(uname -s) == Linux ]]; then  # default prefix only
        $SUDO ldconfig
    fi
}

# The documented recipe (c-library.md, "Runtime loading") for a pkg-config
# program whose libraries are outside the loader's search path: one rpath per
# library the program names. Linux only -- a macOS library names itself by its
# absolute path (gh-1594), so the docs say a Mac program needs nothing, and
# this proves it. Nothing for the default prefix, where `ldconfig` applies.
pc_rpath() { # pkg-config module...
    [[ -n ${PREFIX:-} && $(uname -s) == Linux ]] || return 0
    local m
    for m in "$@"; do
        printf -- '-Wl,-rpath,%s ' "$(pkg-config --variable=libdir "$m")"
    done
}

# Append a line to a table of a TOML file, directly under its header.
toml_add() { # file table line
    python3 - "$@" <<'EOF'
import sys
path, table, line = sys.argv[1:]
text = open(path).read()
header = f"[{table}]\n"
assert text.count(header) == 1, f"{path}: expected one {header!r}"
open(path, "w").write(text.replace(header, header + line + "\n"))
EOF
}

# ── the ratchet ──────────────────────────────────────────────────────────────
# KNOWN_BROKEN names checks that fail today, each with its issue. A listed
# check that fails is reported, not fatal; a listed check that PASSES fails
# the run until it is removed, so the list only shrinks. Each issue's fix
# removes its entry.
KNOWN_BROKEN=" "

expect() { # id, description, command...
    local id=$1 what=$2
    shift 2
    if "$@"; then
        [[ $KNOWN_BROKEN != *" $id "* ]] \
            || die "$id passes now: remove it from KNOWN_BROKEN"
        echo "ok  $what"
    else
        [[ $KNOWN_BROKEN == *" $id "* ]] || die "$what"
        echo "known broken: $what"
    fi
}

cd "$WORK"

# ── alpha: the dependency ────────────────────────────────────────────────────
say "alpha"
tree_jm new alpha --object acore >/dev/null
# A hand-written header at the package root of the include tree, spelled the
# way every include should be: "alpha/alpha_api.h".
mkdir -p alpha/native/inc/alpha
cat >alpha/native/inc/alpha/alpha_api.h <<'EOF'
#ifndef ALPHA_API_H
#define ALPHA_API_H
/* A value type a dependent's own header will use, and some state. */
typedef struct { int k; } alpha_cfg_t;
int alpha_bump (void);
#endif
EOF
cat >>alpha/native/src/acore/acore_core.c <<'EOF'

#include "alpha/alpha_api.h"
static int alpha_count;
int alpha_bump (void) { return ++alpha_count; }
EOF
install_project alpha

# ── beta and gamma: packages that depend on alpha ────────────────────────────
dependent() { # name, component, the [project] declaration, its core's link
    local name=$1 comp=$2 decl=$3 target=$4 guard
    # macOS ships bash 3.2, which has no ${name^^}.
    guard=$(printf '%s_API_H' "$name" | tr '[:lower:]' '[:upper:]')
    say "$name ($decl)"
    tree_jm new "$name" --object "$comp" >/dev/null
    toml_add "$name/just-makeit.toml" project "$decl"
    toml_add "$name/objects/$comp.toml" "$comp" \
        "extra_link_libs = [\"$target\"]"
    (cd "$name" && tree_jm apply >/dev/null)
    # The dependent's PUBLIC header includes alpha's: a consumer compiling it
    # needs alpha's compile usage, which only the dependency metadata carries.
    mkdir -p "$name/native/inc/$name"
    cat >"$name/native/inc/$name/${name}_api.h" <<EOF
#ifndef $guard
#define $guard
#include "alpha/alpha_api.h"
int ${name}_via_alpha (const alpha_cfg_t *cfg);
#endif
EOF
    cat >>"$name/native/src/$comp/${comp}_core.c" <<EOF

#include "$name/${name}_api.h"
int ${name}_via_alpha (const alpha_cfg_t *cfg) { return cfg->k * alpha_bump (); }
EOF
    install_project "$name"
}

dependent beta bcore 'find_packages = [{ name = "alpha", pkg_config = "alpha" }]' \
    'alpha::alpha'
dependent gamma gcore 'pkg_modules = ["alpha >= 0.1"]' 'PkgConfig::ALPHA'

# ── consumers: the official instructions, verbatim ───────────────────────────
# Two programs per dependent, because the docs give two instructions:
#
#   only.c  uses the dependent alone and names only it. Its header pulls in
#           alpha's, so alpha's compile usage -- and, linking static, alpha
#           itself -- must arrive through the dependent's own metadata.
#   both.c  ALSO calls alpha directly, so it names both packages: a program
#           that uses a library's symbols asks for that library itself
#           (pkg-config guide; cmake-packages(7)). It prints "1,2": alpha's
#           counter bumped through the dependent, then directly -- one copy.
consume() { # name
    local name=$1 dir="$WORK/use-$1" static kind link p rp
    mkdir -p "$dir"
    cat >"$dir/only.c" <<EOF
#include <stdio.h>
#include "$name/${name}_api.h"
int main (void)
{
  alpha_cfg_t cfg = { 3 };
  int a = ${name}_via_alpha (&cfg);
  printf ("%d\n", a);
  return !(a == 3);
}
EOF
    cat >"$dir/both.c" <<EOF
#include <stdio.h>
#include "$name/${name}_api.h"
int main (void)
{
  alpha_cfg_t cfg = { 1 };
  int a = ${name}_via_alpha (&cfg), b = alpha_bump ();
  printf ("%d,%d\n", a, b);
  return !(a == 1 && b == 2);
}
EOF
    # Two CMake projects, not one: a find_package(alpha) beside only.c would
    # mask a dependent whose config forgets find_dependency(alpha).
    mkdir -p "$dir/only" "$dir/both"
    cat >"$dir/only/CMakeLists.txt" <<EOF
cmake_minimum_required(VERSION 3.16)
project(only_$name C)
find_package($name REQUIRED)
add_executable(only_shared ../only.c)
target_link_libraries(only_shared PRIVATE $name::$name)
add_executable(only_static ../only.c)
target_link_libraries(only_static PRIVATE $name::${name}-static)
EOF
    cat >"$dir/both/CMakeLists.txt" <<EOF
cmake_minimum_required(VERSION 3.16)
project(both_$name C)
find_package($name REQUIRED)
find_package(alpha REQUIRED)
add_executable(both_shared ../both.c)
target_link_libraries(both_shared PRIVATE $name::$name alpha::alpha)
add_executable(both_static ../both.c)
target_link_libraries(both_static PRIVATE $name::${name}-static
                                          alpha::alpha-static)
EOF
    runs() { # program, expected output
        local out
        out=$("$1" 2>&1) || { echo "  exited $?: $out" | head -3; return 1; }
        [[ $out == "$2" ]] || { echo "  printed '$out', not $2"; return 1; }
    }
    check() { # label, program, expected output, [ratchet id]
        expect "${4:--}" "$name  $1" runs "$2" "$3"
    }
    say "consume $name"
    for static in "" --static; do
        # `--static` only ADDS the private dependencies to the flags; given
        # both libbeta.so and libbeta.a the linker still takes the .so, so
        # without a static link the leg cannot fail for a missing
        # Requires.private. Linux links it fully static (`-static`, as the
        # pkg-config guide's static example intends); macOS has no static
        # libc, so there the leg checks the flags resolve and link.
        link=
        [[ -n $static && $(uname -s) == Linux ]] && link=-static
        rp=
        [[ -z $static ]] && rp=$(pc_rpath "$name")
        # shellcheck disable=SC2046,SC2086 # splitting the flags IS the usage
        $CC "$dir/only.c" $link $(pkg-config $static --cflags --libs "$name") \
            $rp -o "$dir/only-pc$static"
        check "pkg-config $static $link: $name alone" "$dir/only-pc$static" 3
        [[ -z $static ]] && rp=$(pc_rpath "$name" alpha)
        # shellcheck disable=SC2046,SC2086
        $CC "$dir/both.c" $link \
            $(pkg-config $static --cflags --libs "$name" alpha) \
            $rp -o "$dir/both-pc$static"
        check "pkg-config $static $link: $name + alpha" \
            "$dir/both-pc$static" 1,2
    done
    for p in only both; do
        cmake -S "$dir/$p" -B "$dir/$p/build" >/dev/null
        cmake --build "$dir/$p/build" >/dev/null
    done
    for kind in shared static; do
        check "find_package $kind: $name alone" "$dir/only/build/only_$kind" 3
        check "find_package $kind: $name + alpha" \
            "$dir/both/build/both_$kind" 1,2
    done
}

consume beta
consume gamma

# ── delta: one project, more than one library (gh-1600) ──────────────────────
# `[project.libraries.<name>]` installs lib<pkg>_<name> beside lib<pkg>: its own
# .pc (`Requires: delta`) and exported targets delta::<name> /
# delta::<name>-static in delta's package, a COMPONENT of it. `ext` calls INTO
# libdelta; `twin` is guarded to the platforms this runs on, so it takes the
# guarded path and is built; `winonly` is guarded to Windows, so here it must
# be absent -- nothing installed, and a COMPONENTS request for it not found.
say "delta ([project.libraries])"
tree_jm new delta --object dcore >/dev/null
mkdir -p delta/native/inc/delta/ext
cat >delta/native/inc/delta/delta_base.h <<'EOF'
#ifndef DELTA_BASE_H
#define DELTA_BASE_H
int delta_base (int x);
#endif
EOF
cat >>delta/native/src/dcore/dcore_core.c <<'EOF'

#include "delta/delta_base.h"
int delta_base (int x) { return x + 1; }
EOF
cat >delta/native/inc/delta/ext/ext.h <<'EOF'
#ifndef DELTA_EXT_H
#define DELTA_EXT_H
int delta_ext (int x); /* 10 * delta_base (x), from libdelta */
int delta_twin (void);
#endif
EOF
for obj in ext twin winonly; do
    mkdir -p "delta/native/src/$obj"
    cat >"delta/native/src/$obj/CMakeLists.txt" <<EOF
add_library(${obj}_obj OBJECT $obj.c)
set_target_properties(${obj}_obj PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(${obj}_obj PUBLIC \${CMAKE_SOURCE_DIR}/native/inc)
EOF
done
cat >delta/native/src/ext/ext.c <<'EOF'
#include "delta/delta_base.h"
#include "delta/ext/ext.h"
int delta_ext (int x) { return 10 * delta_base (x); }
EOF
echo 'int delta_twin (void) { return 7; }' >delta/native/src/twin/twin.c
echo 'int delta_winonly (void) { return 9; }' >delta/native/src/winonly/winonly.c
toml_add delta/just-makeit.toml project 'c_deps = ["ext", "twin", "winonly"]'
cat >>delta/just-makeit.toml <<'EOF'

[project.libraries.ext]
cores = ["ext_obj"]
description = "delta's extension layer"

[project.libraries.twin]
cores = ["twin_obj"]
platforms = ["linux", "macos"]

[project.libraries.winonly]
cores = ["winonly_obj"]
platforms = ["windows"]
EOF
(cd delta && tree_jm apply >/dev/null)
install_project delta

delta_use="$WORK/use-delta"
mkdir -p "$delta_use/fp"
# One program, both libraries: libdelta_ext's own symbol, which calls into
# libdelta, and libdelta's directly -- both on one link line.
cat >"$delta_use/use.c" <<'EOF'
#include <stdio.h>
#include "delta/delta_base.h"
#include "delta/ext/ext.h"
int main (void)
{
  int e = delta_ext (2), b = delta_base (4), t = delta_twin ();
  printf ("%d,%d,%d\n", e, b, t);
  return !(e == 30 && b == 5 && t == 7);
}
EOF
delta_runs() { # program
    local out
    out=$("$1" 2>&1) || { echo "  exited $?: $out" | head -3; return 1; }
    [[ $out == "30,5,7" ]] || { echo "  printed '$out', not 30,5,7"; return 1; }
}
say "consume delta"
for static in "" --static; do
    link=
    [[ -n $static && $(uname -s) == Linux ]] && link=-static
    # The program names only the additional libraries; libdelta arrives
    # through their `Requires: delta`, which is the point of saying it there.
    rp=
    [[ -z $static ]] && rp=$(pc_rpath delta_ext delta_twin)
    # shellcheck disable=SC2046,SC2086 # splitting the flags IS the usage
    $CC "$delta_use/use.c" $link \
        $(pkg-config $static --cflags --libs delta_ext delta_twin) \
        $rp -o "$delta_use/use-pc$static"
    expect - "delta  pkg-config $static $link: delta_ext delta_twin" \
        delta_runs "$delta_use/use-pc$static"
done

# gh-1869: a program that calls only libdelta_ext, which itself calls libdelta.
# Under --as-needed (Debian's and Ubuntu's gcc default) the program records no
# NEEDED libdelta, so libdelta is found through libdelta_ext's own RUNPATH or
# not at all: a program's RUNPATH does not reach its dependencies' dependencies.
# This is what `$ORIGIN` on the installed library is for -- without it the
# program does not load, and GNU ld does not even link it.
cat >"$delta_use/ext_only.c" <<'EOF'
#include <stdio.h>
#include "delta/ext/ext.h"
int main (void)
{
  int e = delta_ext (2);
  printf ("%d\n", e);
  return !(e == 30);
}
EOF
ext_only_runs() { # program
    local out
    out=$("$1" 2>&1) || { echo "  exited $?: $out" | head -3; return 1; }
    [[ $out == "30" ]] || { echo "  printed '$out', not 30"; return 1; }
}
ext_only_pc() {
    # shellcheck disable=SC2046 # splitting the flags IS the usage
    $CC "$delta_use/ext_only.c" $(pkg-config --cflags --libs delta_ext) \
        $(pc_rpath delta_ext) -o "$delta_use/ext-only-pc" \
        && ext_only_runs "$delta_use/ext-only-pc"
}
expect - "delta  pkg-config: delta_ext alone, libdelta reached through it" \
    ext_only_pc
cat >"$delta_use/fp/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(use_delta C)
find_package(delta REQUIRED COMPONENTS ext twin)
find_package(delta COMPONENTS winonly)
if(delta_winonly_FOUND)
  message(FATAL_ERROR "winonly is Windows-only, yet it was found here")
endif()
add_executable(use_shared ../use.c)
target_link_libraries(use_shared PRIVATE delta::ext delta::twin)
add_executable(use_static ../use.c)
target_link_libraries(use_static PRIVATE delta::ext-static delta::twin-static)
add_executable(ext_only ../ext_only.c)
target_link_libraries(ext_only PRIVATE delta::ext)
EOF
cmake -S "$delta_use/fp" -B "$delta_use/fp/build" >/dev/null
cmake --build "$delta_use/fp/build" >/dev/null
for kind in shared static; do
    expect - "delta  find_package COMPONENTS ext twin, $kind" \
        delta_runs "$delta_use/fp/build/use_$kind"
done
expect - "delta  find_package: delta::ext alone, libdelta reached through it" \
    ext_only_runs "$delta_use/fp/build/ext_only"

# ── one package in a second prefix, its dependency in the first (gh-1869) ───
# beta is installed again, into its own prefix, while alpha stays where it
# is: the layout of two packages installed separately. libbeta finds libalpha
# only through the absolute entry INSTALL_RPATH_USE_LINK_PATH records (`$ORIGIN`
# is beta's own directory), so this is the check that entry is there.
say "beta in a second prefix, alpha in the first"
P2="$WORK/prefix2"
xp="$WORK/use-beta-xprefix"
mkdir -p "$xp/fp"
cmake --install "$WORK/beta/build" --prefix "$P2" >/dev/null
# beta from the second prefix FIRST, so pkg-config and find_package take that
# copy rather than the one beside alpha.
pc2="$P2/lib/pkgconfig:$P2/lib64/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
xprefix_pc() {
    local rp=
    [[ $(uname -s) == Linux ]] \
        && rp="-Wl,-rpath,$(PKG_CONFIG_PATH=$pc2 pkg-config --variable=libdir beta)"
    # shellcheck disable=SC2046,SC2086 # splitting the flags IS the usage
    $CC "$WORK/use-beta/only.c" \
        $(PKG_CONFIG_PATH=$pc2 pkg-config --cflags --libs beta) $rp \
        -o "$xp/only-pc" && runs "$xp/only-pc" 3
}
expect - "beta  pkg-config: beta in its own prefix, alpha in another" \
    xprefix_pc
cat >"$xp/fp/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(only_beta_xprefix C)
find_package(beta REQUIRED)
add_executable(only_shared ../../use-beta/only.c)
target_link_libraries(only_shared PRIVATE beta::beta)
EOF
cmake -S "$xp/fp" -B "$xp/fp/build" \
    "-DCMAKE_PREFIX_PATH=$P2${CMAKE_PREFIX_PATH:+;$CMAKE_PREFIX_PATH}" >/dev/null
cmake --build "$xp/fp/build" >/dev/null
expect - "beta  find_package: beta in its own prefix, alpha in another" \
    runs "$xp/fp/build/only_shared" 3

# `winonly` is not built here, so nothing of it may be installed.
no_winonly() {
    ! grep -q winonly "$WORK/delta/build/install_manifest.txt"
}
expect - "delta  a library guarded to other platforms installs nothing here" \
    no_winonly

# gh-1601's split holds for every library: `runtime` is exactly the versioned
# shared libraries, `dev` everything a build needs -- each additional
# library's .pc and archive included.
component_split() {
    local rt="$WORK/delta-rt" dev="$WORK/delta-dev" f bad=0
    cmake --install "$WORK/delta/build" --component runtime --prefix "$rt" \
        >/dev/null
    cmake --install "$WORK/delta/build" --component dev --prefix "$dev" \
        >/dev/null
    for f in libdelta_ext libdelta_twin libdelta; do
        [[ -n $(find "$rt" -name "$f.so.*" -o -name "$f.*.dylib") ]] \
            || { echo "  runtime lacks $f"; bad=1; }
    done
    while IFS= read -r f; do
        case $f in
            *.so.* | *.dylib) ;;
            *) echo "  runtime has $f"; bad=1 ;;
        esac
    done < <(find "$rt" -type f)
    for f in delta_ext.pc delta_twin.pc libdelta_ext.a libdelta_twin.a; do
        [[ -n $(find "$dev" -name "$f") ]] || { echo "  dev lacks $f"; bad=1; }
    done
    return $bad
}
expect - "delta  cmake --install --component runtime|dev splits every library" \
    component_split

# ── packages side by side (gh-1583) ──────────────────────────────────────────
# A jm package and its jm dependencies share one prefix and one consumer. Each
# must own its files, and a consumer must name each one's headers without
# ambiguity: `#include "<pkg>/<comp>/<comp>_core.h"` with -I${includedir}.
#

# No file one package installs may be a file another installed: the second
# install overwrites it, and the first package's consumers get the other's.
disjoint_installs() {
    local clash
    clash=$(cat "$WORK"/{alpha,beta,gamma,delta}/build/install_manifest.txt \
        | sort | uniq -d)
    [[ -z $clash ]] || { printf '%s\n' "$clash" | sed 's/^/  /'; return 1; }
}

# One translation unit including a generated header from each package, by
# the prefixed path, with only the flags pkg-config hands out.
prefixed_include() {
    local tu="$WORK/side-by-side.c"
    printf '%s\n' '#include "alpha/acore/acore_core.h"' \
        '#include "beta/bcore/bcore_core.h"' 'int main (void) { return 0; }' \
        >"$tu"
    # shellcheck disable=SC2046 # splitting the flags IS the usage
    $CC -c "$tu" $(pkg-config --cflags beta alpha) -o "$WORK/side-by-side.o" \
        2>"$WORK/side-by-side.err" \
        || { sed 's/^/  /' "$WORK/side-by-side.err" | head -5; return 1; }
}

say "packages side by side"
expect disjoint-install "alpha, beta and gamma install disjoint files" \
    disjoint_installs
expect prefixed-include "\"alpha/acore/...\" and \"beta/bcore/...\" in one TU" \
    prefixed_include

# ── one component name, two packages (gh-1591) ───────────────────────────────
# pa and pb both have a component `fir`. Unprefixed, they collide on every
# symbol jm derives -- and in one translation unit the shared FIR_CORE_H guard
# silently drops the second header. `c_prefix` namespaces each: pa_fir_* and
# pb_fir_*, PA_/PB_FIR_CORE_H. pb's create() keeps gain x 1000, so a program
# that resolved one package's fir to the other's prints the wrong number rather
# than merely failing to link.
say "pa and pb: one component name, two packages ([project] c_prefix)"
for p in pa pb; do
    tree_jm new "$p" --c-prefix "$p" --object fir --state gain:double:1.0 \
        >/dev/null
done
sed -i.bak 's/obj->gain = gain;/obj->gain = gain * 1000.0;/' \
    pb/native/src/fir/fir_core.c
grep -q 'gain \* 1000.0' pb/native/src/fir/fir_core.c \
    || die "pb: the create() edit did not land"
install_project pa
install_project pb

cat >"$WORK/twofir.c" <<'EOF'
#include <stdio.h>
#include "pa/fir/fir_core.h"
#include "pb/fir/fir_core.h"
int main (void)
{
  pa_fir_state_t *a = pa_fir_create (2.0);
  pb_fir_state_t *b = pb_fir_create (2.0);
  printf ("%g,%g\n", pa_fir_get_gain (a), pb_fir_get_gain (b));
  pa_fir_destroy (a);
  pb_fir_destroy (b);
  return 0;
}
EOF
twofir() { # label, extra link words...
    local out exe="$WORK/twofir-$1"
    shift
    # shellcheck disable=SC2046,SC2086 # splitting the flags IS the usage
    $CC "$WORK/twofir.c" "$@" -o "$exe" 2>"$exe.err" \
        || { sed 's/^/  /' "$exe.err" | head -5; return 1; }
    out=$("$exe" 2>&1) || { echo "  exited: $out"; return 1; }
    [[ $out == "2,2000" ]] || { echo "  printed '$out', not 2,2000"; return 1; }
}
whole_archive() { # the two static libraries, every member pulled in
    local da db
    da=$(pkg-config --variable=libdir pa)
    db=$(pkg-config --variable=libdir pb)
    if [[ $(uname -s) == Darwin ]]; then
        echo "-Wl,-force_load,$da/libpa.a -Wl,-force_load,$db/libpb.a"
    else
        echo "-Wl,--whole-archive $da/libpa.a $db/libpb.a -Wl,--no-whole-archive"
    fi
}
# shellcheck disable=SC2046
expect twofir-shared "pa + pb, one TU, shared: each fir is its own" \
    twofir shared $(pkg-config --cflags --libs pa pb) $(pc_rpath pa pb)
static_link=
[[ $(uname -s) == Linux ]] && static_link=-static
# shellcheck disable=SC2046,SC2086
expect twofir-static "pa + pb, one TU, static $static_link: each fir is its own" \
    twofir static $static_link $(pkg-config --static --cflags --libs pa pb)
# shellcheck disable=SC2046,SC2086
expect twofir-whole "pa + pb, one TU, whole archives: no duplicate symbol" \
    twofir whole $(pkg-config --cflags pa pb) $(whole_archive) \
    $(pkg-config --static --libs-only-l pa pb | tr ' ' '\n' \
        | grep -vx -e -lpa -e -lpb -e '')

say "every consumer built and ran"
