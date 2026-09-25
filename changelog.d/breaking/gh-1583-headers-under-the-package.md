- **A project's headers live under `native/inc/<pkg>/`, and every include of
    one is spelled `"<pkg>/..."`** (gh-1583, schema 8). An installed project's
    headers land in `include/<pkg>/`, so two jm projects in one prefix no
    longer collide over a component name, `clib_common.h` or `jm_perf.h`;
    `-I` is still `native/inc`. `jm new` scaffolds the new layout, and `jm   upgrade` moves an existing project: it moves `native/inc/*` one level down
    and respells every `#include`, manifest string (`header =`, `core_header   =`) and CMake path that resolves to a moved file, and prints each file it
    changed. A header you add from now on goes under `native/inc/<pkg>/`, and
    a consumer includes `"<pkg>/<comp>/<comp>_core.h"`. See
    docs/upgrading.md.
