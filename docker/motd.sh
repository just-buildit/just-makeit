#!/bin/sh
# Printed once per interactive shell session.
#
# This used to BE the welcome text, as a hand-written heredoc. It went stale:
# it advertised `my_corr/`, which no example produces, omitted ~25 project
# directories that do exist, and told the reader to run
# `python3 -m just_makeit._example_readme`, a module that does not exist.
#
# So the text now has exactly one source — `$JM_HOME/README.md`, generated at
# image build time by `welcome.py` from the projects that actually built. The
# editor opens that file and this prints it, which is why there is nothing
# here to drift.
JM_README="${JM_HOME:-/home/just-makeit}/README.md"

if [ -r "$JM_README" ]; then
    printf '\n'
    cat "$JM_README"
    printf '\n'
else
    # Only reachable if the image was assembled without the build step that
    # writes it, so say that rather than printing a cheerful nothing.
    printf '\n  just-makeit sandbox: %s is missing.\n' "$JM_README"
    printf '  The image build did not complete; try `ls ~/examples`.\n\n'
fi
