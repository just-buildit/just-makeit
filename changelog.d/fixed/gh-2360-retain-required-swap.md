- **A required retained capsule param has no dead detach branch**
    (gh-2360). A param declaring both `retain = true` and `required = true`
    is now kept unconditionally. The binding has already refused `None`, so
    the swap's `None` branch could not run, and its comment claimed `None`
    detaches the owner.
