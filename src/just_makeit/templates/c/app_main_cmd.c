// /*<<project>>*/ — /*<<name>>*/: multi-command CLI (scaffolded by just-makeit).
// Build:  make && ./build//*<<name>>*/
// Re-running `just-makeit app` overwrites this file; fill each command body.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/*<<command_handlers>>*/
static void
usage(void)
{
    fprintf(stderr, "/*<<usage>>*/\n");
}

int
main(int argc, char *argv[])
{
    if (argc < 2) {
        usage();
        return 2;
    }
/*<<dispatch>>*/
    fprintf(stderr, "unknown command: %s\n", argv[1]);
    usage();
    return 2;
}
