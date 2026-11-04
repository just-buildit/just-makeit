- **`[project] layout = "nested"` puts a component's header under its
    module's path, and every mirror of it follows** (gh-2427). An object of
    `[module."rx.ss.burst"]` kept its header at `native/inc/<pkg>/fir/`
    whatever module it belonged to, so the C tree and the Python package
    disagreed. Under `nested` it is `native/inc/<pkg>/rx/ss/burst/fir/`, the
    module's path read from the one answer to where its Python lives
    (`_config.module_package_resolved`, so a declared `package` wins), and
    the C test, benchmark, every generated `#include` (umbrella, binding,
    tests, benches, a `depends_on` object's include of its dependency, the
    bridge and process-global headers) and the CMake include directories
    follow it through `_incpath.core_dir`. Switching an existing project
    either way is the key and `jm apply` (or `jm upgrade`): the headers move
    with your edits, a header of yours beside the core comes along, and each
    `#include` that resolves to a moved header is respelled; changing a
    module's `package` under `nested` is the same move, and `apply` reports
    each as a rename, not a new file. `jm remove` takes only the removed
    component's own files when another component's directory sits below
    its own. `flat` and
    `mirrored` render byte-identically to before. `jm remove module` now also
    removes a function-bearing module's own C test and benchmark, which it
    left behind including the header it had just deleted.
