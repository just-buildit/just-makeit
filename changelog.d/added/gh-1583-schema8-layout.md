- **A schema-8 project keeps its headers under `native/inc/<pkg>/`**
    (gh-1583, part 2), so an installed project's headers live under
    `include/<pkg>/` and every include is spelled `"<pkg>/..."`: two
    projects installed into one prefix no longer overwrite each other's
    `clib_common.h`, and one translation unit can include both. Not yet the
    default: `jm new` still writes schema 7, and nothing changes for an
    existing project until `jm upgrade` can migrate it (part 3).
