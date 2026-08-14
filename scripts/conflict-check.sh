#!/bin/sh
# Fail on a merge-conflict marker left in a tracked text file.
#
# gh-974: docs/configuration.md carried one for ten days and a release, and it
# rendered on the published docs site. Nothing caught it, and the reason is
# worth the extra patterns below: **mdformat normalises the markers rather than
# refusing them**, so each pass through `make format` made the corruption
# harder to see, not easier.
#
# What that does to the three markers:
#
#   <<<<<<< HEAD          ->  \<<\<<\<<< HEAD        (every `<` escaped)
#   =======               ->  a setext H1 — the line ABOVE it becomes a
#                             heading and the `=======` disappears entirely
#   >>>>>>> d19e3ae (...) ->  > > > > > > > d19e3ae  (seven blockquotes)
#
# So a check written against the literal three markers would have found one of
# the three in that file, and the `=======` case cannot be found after the
# fact at all — which is why this runs over the *tracked* set on every lint,
# where the raw form is still there to catch on the way in.
#
# Usage: conflict-check.sh [file...]      (no arguments: every tracked file)
#
# Deliberately anchored at column 1. A marker indented inside a fenced code
# block is documentation about conflicts — CHANGELOG.md quotes one in a worked
# example of `jm apply` refusing a corrupted stub — and git never writes one
# indented.
set -eu

pattern='^(<{7}([ ]|$)|>{7}([ ]|$)|={7}$|\\<<\\<<\\<<<|(> ){7})'

if [ "$#" -gt 0 ]; then
    hits=$(grep -nIE "$pattern" "$@" 2>/dev/null || true)
else
    hits=$(git ls-files -z | xargs -0 grep -nIE "$pattern" 2>/dev/null || true)
fi

if [ -n "$hits" ]; then
    echo "ERROR: merge-conflict marker(s) in tracked file(s):"
    printf '%s\n' "$hits" | sed 's/^/  /'
    echo ""
    echo "  Resolve the conflict. If a line here is deliberate prose about"
    echo "  conflicts, indent it inside a fenced code block — this only looks"
    echo "  at column 1."
    exit 1
fi

echo "conflict-check: no conflict markers in tracked files"
