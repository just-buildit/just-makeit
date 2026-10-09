- **A make-backend project links its extension on macOS** (gh-2169). The
    extension was linked with `-shared` alone, which leaves the interpreter's
    `_PyCapsule_*` symbols undefined on a Mach-O bundle, so the link failed there
    and a make-backend project had never built on macOS. The link now adds
    `-undefined dynamic_lookup` on Darwin, chosen by `uname -s`. A project
    scaffolded before this keeps its old Makefile: it is create-only (see #2164).
