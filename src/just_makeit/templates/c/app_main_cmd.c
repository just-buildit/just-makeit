// /*<<project>>*/ — /*<<name>>*/: multi-command CLI (scaffolded by just-makeit).
// Build:  make && ./build//*<<name>>*/
// Regenerated from `[app]` by `just-makeit app` AND by every `just-makeit
// apply` — edits here are discarded, command bodies included. Put the
// implementation in a component (`jm method`) and call it from each body.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/*<<helpers>>*/
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
