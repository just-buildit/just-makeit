- **`jbx just-makeit:install-deps` runs jm's installer.** The published
    site now ships `aliases.toml` beside `install.sh`, aliasing
    `install-deps` to `install.sh`, which is where jbx's `just-makeit`
    namespace looks first. `make docs` copies it into `site/`, and a test
    runs the shared `site-files` recipe and holds every alias to a file it
    ships.
