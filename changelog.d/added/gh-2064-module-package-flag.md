- **`jm module <name> --package <dir>` declares `[module.X] package` when the
    module is created** (gh-2064). The key (gh-523) had no CLI flag, so the
    documented route was `jm module mod` and then adding `package = "other"`
    to the manifest. By then `jm module` had already written
    `src/<pkg>/mod/__init__.py` and `mod.pyi`. `jm apply` then wrote the
    module into `src/<pkg>/other/` and left those two files behind. No
    command owned them and `status --check` read clean. The flag writes the
    key before any file, so nothing lands in the module's own directory.
    `jm script` replays the key as `--package` instead of a NOTE asking you
    to add it back by hand. The key still has to be set when the module is
    created: added to an existing module, it leaves the old directory
    behind (gh-2081).
