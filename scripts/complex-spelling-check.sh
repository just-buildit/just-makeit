#!/bin/sh
# Fail on the `complex` spelling of a C complex type in a tracked file.
#
# gh-1246 changed generated C from the `<complex.h>` macro spelling to
# `_Complex` (this comment says it that way on purpose -- writing the old
# token here would make this script fail its own check, and a gate that has
# to exempt itself is weaker than one that does not) and
# deleted the `#define complex _Complex` shim that made the old spelling parse
# from C++. The sweep that came with it covered `tests/`, `examples/` and
# `docs/`, and the review that checked the sweep counted the same three trees.
#
# Neither looked in `.github/`. `artifact.yml`'s pre-publish smoke patches a
# generated stub through a regex anchored on the old spelling, so it matched
# nothing and asserted `stub not found` on all TWELVE legs. That is where it
# surfaced: not in a PR, but in the v0.74.0 release run, after the tag was
# pushed. The publish gate held and nothing reached PyPI, which is the only
# reason this was cheap.
#
# A second copy of the same patching logic lives in the SHIPPED
# `scripts/docker-e2e.sh`, and it had the same defect.
#
# So the lesson is not "grep harder next time" -- it is that a generated-output
# change reaches workflow files and shipped scripts, and nothing was looking
# there. This looks at every tracked file and names the ones allowed to differ.
#
# `complex` typed by an AUTHOR is still accepted everywhere jm reads a type;
# this is only about what jm WRITES and what our own tooling expects to read.
#
# Usage: complex-spelling-check.sh [file...]   (no arguments: every tracked file)
set -eu

# Files where the old spelling is deliberate. Each needs its reason here; an
# entry whose file no longer contains it is reported, so this cannot rot into
# a list of names nobody can justify.
allowed_reason() {
    case "$1" in
    src/just_makeit/_types.py)
        echo "the author-facing INPUT alias table -- it must name the spelling it resolves" ;;
    src/just_makeit/_bind.py)
        echo "maps the spelling back when parsing a hand-written header" ;;
    src/just_makeit/templates/c/inc/clib_common.h)
        echo "the comment quotes the old spelling while explaining why it was a problem" ;;
    CHANGELOG.md)
        echo "historical entries describe what past releases emitted" ;;
    tests/test_gh595_unknown_return_type.py)
        echo "asserts the type registry rejects it as a STORED key" ;;
    src/just_makeit/_upgrade.py)
        echo "the gh-1248 migration -- it must name the spelling it replaces" ;;
    tests/test_gh1248_upgrade_complex_spelling.py)
        echo "builds a pre-gh-1246 tree in order to migrate it" ;;
    *) return 1 ;;
    esac
}

pattern='\b(float|double|long double) complex\b'

if [ "$#" -gt 0 ]; then
    hits=$(grep -lIE "$pattern" "$@" 2>/dev/null || true)
else
    hits=$(git ls-files -z | xargs -0 grep -lIE "$pattern" 2>/dev/null || true)
fi

bad=""
for f in $hits; do
    allowed_reason "$f" >/dev/null 2>&1 || bad="$bad $f"
done

if [ -n "$bad" ]; then
    echo "ERROR: the pre-gh-1246 complex spelling in tracked file(s):"
    for f in $bad; do
        grep -nIE "$pattern" "$f" | head -3 | sed "s|^|  $f:|"
    done
    echo ""
    echo "  jm emits \`_Complex\`. A file that expects \`complex\` in GENERATED"
    echo "  output is wrong -- including CI workflows and shipped scripts,"
    echo "  which is how this reached a release run (gh-1246)."
    echo "  If the spelling is deliberate, add the file to allowed_reason()"
    echo "  in this script with the reason."
    exit 1
fi

# A stale allow-list entry is its own failure: it reads as a justified
# exception when the justification no longer applies.
stale=""
for f in src/just_makeit/_types.py src/just_makeit/_bind.py \
         src/just_makeit/templates/c/inc/clib_common.h CHANGELOG.md \
         tests/test_gh595_unknown_return_type.py \
         src/just_makeit/_upgrade.py \
         tests/test_gh1248_upgrade_complex_spelling.py; do
    [ -f "$f" ] || continue
    grep -qIE "$pattern" "$f" || stale="$stale $f"
done
if [ -n "$stale" ]; then
    echo "ERROR: allow-list entr(ies) no longer contain the spelling:$stale"
    echo "  Remove them from allowed_reason() -- an exception nobody needs"
    echo "  reads as one somebody justified."
    exit 1
fi

echo "complex-spelling-check: jm's \`_Complex\` spelling holds outside 7 named files"
