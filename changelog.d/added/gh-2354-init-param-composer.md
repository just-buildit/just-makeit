- **An `init_param` can name a composer: `object = "<composer>"`** (gh-2354).
    gh-2346 let a composer module publish a `_capsule` and a consumer method
    param take it; the constructor path, the `object =` sugar over the capsule
    path, still refused a composer as "not a valid target", so an object could
    not take a composer at construction. It resolves now, as a handle does
    (gh-1227), every slot read from the module's own declarations: the C type
    is the backing's `<stem>_state_t *` (moved by a `c_prefix`), the capsule
    is the one the module publishes, the header is the backing's core header,
    and the class is the composer type. A composer that publishes no capsule
    is refused with the line that fixes it. The consumer needs no `retain`:
    a constructor's borrowed pointer already holds the Python owner. The
    generated `.pyi` imports the class from the file that holds it
    (`from .playlist.playlist import Mix`), not from the composer's package,
    which has no `__init__.py` re-exporting it.
