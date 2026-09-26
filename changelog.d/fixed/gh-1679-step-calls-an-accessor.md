- **An inline `step()` body can call a property getter or a method**
    (gh-1679). The sacred `_core.h` declared accessors and methods below
    the `static inline` step, so an `impl = "return lo_get_gain(state) * x;"`
    failed to compile ("implicit declaration", then "conflicting types"),
    whether the manifest declared the property or `jm property` added it
    later. Every declaration now goes above the step. A header-only
    component declares each of its `static inline` definitions above the
    step as a `static inline` prototype. Existing headers are sacred and
    keep their layout; a declaration jm adds to one from now on goes above
    the step.
