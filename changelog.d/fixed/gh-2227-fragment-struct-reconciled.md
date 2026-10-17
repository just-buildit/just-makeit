- **`apply` keeps a module fragment's object struct in step with the
    manifest** (gh-2227). A member `apply` added to a sacred
    `<mod>_ext_<obj>.c` that writes a field jm declares (a borrow's
    `_jm_borrowed`, a released call's `_jm_inflight`, a retained owner) did
    not compile, because the struct was never revisited. `apply` now adds
    the jm fields the render declares and removes the ones it no longer
    does, keeping any member you added, and refreshes `tp_dealloc` as every
    verb already did.
