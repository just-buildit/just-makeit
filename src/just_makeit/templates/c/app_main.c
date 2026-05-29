// /*<<project>>*/ standalone entry point — scaffolded by just-makeit.
// Build:  make && ./build//*<<name>>*/
// Implement the I/O loop marked below.

#include <stdio.h>
#include <stdlib.h>

#include "/*<<component>>*///*<<component>>*/_core.h"

int
main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    /* --- create ---------------------------------------------------------- */
    /*<<app_create_line>>*/
    if (!state) {
        fprintf(stderr, "error: /*<<component>>*/_create() failed\n");
        return 1;
    }

    /* --- process --------------------------------------------------------- */
    /* <<IMPLEMENT: read stdin, call step() or steps(), write stdout>> */

    /* --- cleanup --------------------------------------------------------- */
    /*<<component>>*/_destroy(state);
    return 0;
}
