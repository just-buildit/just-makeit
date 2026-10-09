- **A project holds several apps, and `jm remove app <name>` takes one
    out** (gh-2074). Each `jm app` appends an `[[app]]` row, keyed by its
    `--name`, so a C binary, a console script and a PEP 723 script over one
    core are three apps, and `jm apply` re-renders every one; the root
    `CMakeLists.txt`'s App block holds an executable per C app. Before, the
    manifest held one `[app]` table: a second `jm app` replaced the first and
    left its files owned by nothing, with `jm status --check` reading clean.
    `jm remove app <name>` deletes the app's file and its wiring -- its lines
    in the App block, a console app's `[project.scripts]` entry -- and keeps a
    file you edited, with a note, as `jm remove method` keeps an authored
    body. `jm script` replays every app.
