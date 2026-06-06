// /*<<project>>*/ — /*<<name>>*/: /*<<Component>>*/-powered stream tool.
// Scaffolded by just-makeit.  Build:  make && ./build//*<<name>>*/
// Re-running `just-makeit app` overwrites this file; edit for custom logic.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "/*<<component>>*///*<<component>>*/_core.h"
/*<<helpers>>*/
int
main(int argc, char *argv[])
{
    /* --- parse args ------------------------------------------------------ */
/*<<arg_parse_block>>*/

    /* --- create ---------------------------------------------------------- */
/*<<app_create_line>>*/
    if (!state) {
        fprintf(stderr, "error: /*<<component>>*/_create() failed\n");
        return 1;
    }

    /* --- process --------------------------------------------------------- */
/*<<io_loop>>*/

    /* --- cleanup --------------------------------------------------------- */
    /*<<component>>*/_destroy(state);
/*<<cleanup_tail>>*/
    return 0;
}
