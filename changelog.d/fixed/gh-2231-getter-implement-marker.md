- **A property getter's binding no longer carries an `<<IMPLEMENT>>`
    marker** (gh-2231). A property backed by a C getter call rendered
    `/* <<IMPLEMENT: return the computed or stored value>> */` in the
    generated binding, directly above the finished
    `return ...(<comp>_get_<prop>(self->handle));`. That was in a standalone
    object's `_ext.c`, a module object's `_ext_<obj>.c` fragment and a
    view's fragment. The marker tells the author which body to write, and
    there it pointed at nothing: the stub the author fills in is the C
    getter in `_core.c`, which keeps its own. `apply` drops the line from an
    `_ext.c`. A fragment is not rewritten wholesale, so `status` reports one
    rendered before this as UNEXPLAINED with every unit identical until it
    is regenerated. `tests/test_gh2231_no_implement_marker_in_glue.py`
    builds the broad fixture tree and refuses the marker in every file jm's
    ownership table calls jm's content.
