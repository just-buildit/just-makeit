- **A capsule an object or handle lends keeps that object alive**
    (gh-2328). The capsule held no reference to the object that lent it,
    so a constructor capsule param (gh-790) or a retained method param
    passed `obj._capsule` rather than `obj` kept only the capsule, and the
    object could be collected under the pointer. The capsule now holds its
    lender; its destructor releases that reference and still never frees
    the pointer.
