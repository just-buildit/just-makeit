- **Install components: `runtime` and `dev`** (gh-1601). jm's install rules
    now carry the split a distribution builds `lib<name>` and
    `lib<name>-dev` from: `cmake --install --component runtime` installs
    exactly the shared library a program loads, `--component dev` the
    headers, static library, unversioned `.so` link, CMake package and `.pc`.
    A plain `cmake --install` installs both, as before. The rules live in
    the managed install block, so an existing project gets them on `apply`.
