#!/usr/bin/env bash
# gh-1590: install jm packages the documented way and consume them by the
# OFFICIAL pkg-config and CMake instructions -- the acceptance test for epic
# gh-1584. It reads top to bottom as what a user would type.
#
#   alpha   a jm package
#   beta    depends on alpha through [project] find_packages (pkg_config)
#   gamma   depends on alpha through [project] pkg_modules, version-bounded
#
# Each dependent's public header includes alpha's, and each is consumed by
# `pkg-config [--static] --cflags --libs` and by `find_package` with the
# shared and static targets -- every program built and RUN (consume()).
#
# CONSUMER_SMOKE_DEFAULT_PREFIX=1 (CI only): install to CMake's DEFAULT
#   prefix (/usr/local), with sudo when it is not writable, and consume with
#   NO hints at all -- no PKG_CONFIG_PATH, no CMAKE_PREFIX_PATH. That is the
#   standard layout, and the only honest test of it is a throwaway machine.
# Otherwise (a developer's box, `make gates`): install to PREFIX, default a
#   fresh temp dir, and add exactly the hints the official docs prescribe for
#   a non-default prefix -- printed, never silent. Nothing outside WORK and
#   PREFIX is touched.
#
# CONSUMER_CMAKE_VERSION=3.16.3 runs everything with Kitware's release binary
#   of that version: the floor every generated project declares.

set -euo pipefail

JM=${JM:-just-makeit}
WORK=${WORK:-$(mktemp -d)}
mkdir -p "$WORK"
CC=${CC:-cc}

say() { printf '\n==> %s\n' "$*"; }
die() {
    printf '::error::%s\n' "$*" >&2
    exit 1
}

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
# reason: the point is what works WITHOUT one.
unset PKG_CONFIG_PATH CMAKE_PREFIX_PATH

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
    # (PKG_CONFIG_PATH), cmake-packages(7) (CMAKE_PREFIX_PATH), and the
    # loader for a shared library outside its search path.
    # lib64 too: GNUInstallDirs picks it on Fedora-family hosts.
    export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib64/pkgconfig"
    export CMAKE_PREFIX_PATH="$PREFIX"
    export LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64"
    export DYLD_LIBRARY_PATH="$PREFIX/lib"
    say "non-default prefix $PREFIX: PKG_CONFIG_PATH, CMAKE_PREFIX_PATH set"
fi

# The documented build and install of a CMake project.
install_project() {
    cmake -S "$1" -B "$1/build" -DBUILD_PYTHON=OFF "${CONFIGURE_PREFIX[@]}"
    cmake --build "$1/build"
    $SUDO cmake --install "$1/build"
    # A new shared library under /usr/local/lib is found once the loader's
    # cache knows it (ldconfig(8)); macOS's dyld searches /usr/local/lib itself.
    if [[ -z ${PREFIX:-} && $(uname -s) == Linux ]]; then  # default prefix only
        $SUDO ldconfig
    fi
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

cd "$WORK"

# ── alpha: the dependency ────────────────────────────────────────────────────
say "alpha"
"$JM" new alpha --object alpha >/dev/null
cat >alpha/native/inc/alpha/alpha_api.h <<'EOF'
#ifndef ALPHA_API_H
#define ALPHA_API_H
/* A value type a dependent's own header will use, and some state. */
typedef struct { int k; } alpha_cfg_t;
int alpha_bump (void);
#endif
EOF
cat >>alpha/native/src/alpha/alpha_core.c <<'EOF'

#include "alpha/alpha_api.h"
static int alpha_count;
int alpha_bump (void) { return ++alpha_count; }
EOF
install_project alpha

# ── beta and gamma: packages that depend on alpha ────────────────────────────
dependent() { # name, the [project] declaration, the target its core links
    local name=$1 decl=$2 target=$3 guard
    # macOS ships bash 3.2, which has no ${name^^}.
    guard=$(printf '%s_API_H' "$name" | tr '[:lower:]' '[:upper:]')
    say "$name ($decl)"
    "$JM" new "$name" --object "$name" >/dev/null
    toml_add "$name/just-makeit.toml" project "$decl"
    toml_add "$name/objects/$name.toml" "$name" \
        "extra_link_libs = [\"$target\"]"
    (cd "$name" && "$JM" apply >/dev/null)
    # The dependent's PUBLIC header includes alpha's: a consumer compiling it
    # needs alpha's compile usage, which only the dependency metadata carries.
    cat >"$name/native/inc/$name/${name}_api.h" <<EOF
#ifndef $guard
#define $guard
#include "alpha/alpha_api.h"
int ${name}_via_alpha (const alpha_cfg_t *cfg);
#endif
EOF
    cat >>"$name/native/src/$name/${name}_core.c" <<EOF

#include "$name/${name}_api.h"
int ${name}_via_alpha (const alpha_cfg_t *cfg) { return cfg->k * alpha_bump (); }
EOF
    install_project "$name"
}

dependent beta 'find_packages = [{ name = "alpha", pkg_config = "alpha" }]' \
    'alpha::alpha_lib'
dependent gamma 'pkg_modules = ["alpha >= 0.1"]' 'PkgConfig::ALPHA'

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
    local name=$1 dir="$WORK/use-$1" static kind link p
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
target_link_libraries(only_shared PRIVATE $name::${name}_lib)
add_executable(only_static ../only.c)
target_link_libraries(only_static PRIVATE $name::${name}_lib_static)
EOF
    cat >"$dir/both/CMakeLists.txt" <<EOF
cmake_minimum_required(VERSION 3.16)
project(both_$name C)
find_package($name REQUIRED)
find_package(alpha REQUIRED)
add_executable(both_shared ../both.c)
target_link_libraries(both_shared PRIVATE $name::${name}_lib alpha::alpha_lib)
add_executable(both_static ../both.c)
target_link_libraries(both_static PRIVATE $name::${name}_lib_static
                                          alpha::alpha_lib_static)
EOF
    check() { # label, program, expected output
        local out
        out=$("$2") || die "$name, $1: exited $? (printed '$out')"
        [[ $out == "$3" ]] || die "$name, $1: printed '$out', not $3"
        echo "ok  $name  $1"
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
        # shellcheck disable=SC2046,SC2086 # splitting the flags IS the usage
        $CC "$dir/only.c" $link $(pkg-config $static --cflags --libs "$name") \
            -o "$dir/only-pc$static"
        check "pkg-config $static $link: $name alone" "$dir/only-pc$static" 3
        # shellcheck disable=SC2046,SC2086
        $CC "$dir/both.c" $link \
            $(pkg-config $static --cflags --libs "$name" alpha) \
            -o "$dir/both-pc$static"
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
say "every consumer built and ran"
