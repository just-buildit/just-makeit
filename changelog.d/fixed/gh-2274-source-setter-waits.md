- **A composer source's buffer setter no longer frees a buffer a running
    generator may borrow** (gh-2274). Assigning a `bytes`, complex-stream or
    owned-pointer field freed the old buffer and installed the new one with
    no wait for a `steps()` running GIL-free on another thread, and left the
    generator built from the old configuration in place. The setter and
    `__init__` now go through one generated replace per field: it converts
    the new value into a copy first (Python can run there; nothing live is
    touched, so a refused value leaves the field as it was, where it used to
    clear it), waits for the running `steps()`, drops the generator so the
    next `steps()` rebuilds it from the new configuration, then frees and
    installs in pure C.
