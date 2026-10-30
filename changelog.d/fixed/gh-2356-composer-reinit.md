- **A composer's second `__init__` no longer leaks its previous state**
    (gh-2356). The generated `__init__` set the state pointer to NULL before
    building a new one, so calling it again on a live composer dropped the
    old state without freeing it, and a re-init that then failed left the
    composer with a NULL state. It now follows the contract a handle has:
    the new state is built first, so a re-init that fails changes nothing;
    then the old state is released, with new calls refused and a running
    GIL-free call waited for, and only if it is still live (`close()` frees
    without clearing the pointer, so a closed composer's is not freed again).
    A composer whose state was lent as a capsule refuses a second `__init__`
    with a `RuntimeError`, since a borrower holds the pointer and there is
    one slot. The refusal is sticky once the state has been lent, and it is checked
    again after the arguments are converted (which can run Python that lends
    it). **Behaviour change:** a composer that has published a capsule can
    never be re-initialised.
