- **A project can install more than one library** (gh-1600).
    `[project.libraries.<name>]` names OBJECT libraries (`cores`), an
    optional `description` and optional `platforms`, and installs
    `lib<pkg>_<name>` beside `lib<pkg>`: its own `<pkg>_<name>.pc` with
    `Requires: <pkg>`, and exported targets `<pkg>::<name>` /
    `<pkg>::<name>-static` in the same package, found with
    `find_package(<pkg> COMPONENTS <name>)`. Every per-library rule (soname,
    install name, install components, the install-time `.pc` prefix) now runs
    over one list of the project's libraries, so an additional library gets
    exactly what `lib<pkg>` gets. `apply` refuses a core the tree does not
    declare, one claimed by two libraries, and one also folded into
    `lib<pkg>`. The packaging templates (`<pkg>.pc.in`,
    `<pkg>-config.cmake.in`) change; `apply` renders jm-owned ones.
