// /*<<project>>*/ — /*<<name>>*/: /*<<Component>>*/-powered stream tool.
// Scaffolded by just-makeit.  Build:  make && ./build//*<<name>>*/
// Re-running `just-makeit app` overwrites this file; edit for custom logic.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "/*<<component>>*///*<<component>>*/_core.h"
/*<<helpers>>*/
/* gh-944: `x != std*` is the standard idiom for "close it only if we opened
 * it", and it is correct. clang-analyzer-unix.Stream still reports a leak,
 * because to reach it the analyzer explores a path on which fopen SUCCEEDED
 * and then assumes its result equals stdin -- "Assuming 'in' is equal to
 * 'stdin'", in its own trace. fopen never returns a std stream, so that path
 * does not exist; nothing in `FILE *in = p ? fopen(p) : stdin` lets the
 * analyzer prove it.
 *
 * Scoped to this function with the reason, rather than switched off in the
 * shipped .clang-tidy: the same check found two REAL leaks in this file -- the
 * OOM bail-out and the create()-failed bail-out, both fixed in this change --
 * so disabling it project-wide would have cost more than it saved. The region
 * wraps main() because the diagnostic is reported wherever the path ends,
 * which is not always a line the closes are on. */
/* NOLINTBEGIN(clang-analyzer-unix.Stream) */
int
main(int argc, char *argv[])
{
    /* --- parse args ------------------------------------------------------ */
/*<<arg_parse_block>>*/

    /* --- create ---------------------------------------------------------- */
/*<<app_create_line>>*/
    if (!state) {
        fprintf(stderr, "error: /*<<component>>*/_create() failed\n");
/*<<cleanup_tail_deep>>*/
        return 1;
    }

    /* --- process --------------------------------------------------------- */
/*<<io_loop>>*/

    /* --- cleanup --------------------------------------------------------- */
    /*<<component>>*/_destroy(state);
/*<<cleanup_tail>>*/
    return 0;
}
/* NOLINTEND(clang-analyzer-unix.Stream) */
