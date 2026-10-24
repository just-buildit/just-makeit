- **A capsule-only lender's teardown comments name the capsule** (gh-2346).
    An object that lends only its pointer as `_capsule` parks its state at
    `destroy()`, but the generated C comments in `destroy()` and the
    deallocator said "the views this object lent". They now say the capsule.
    A view-lending object's comments are unchanged, and no code changed.
