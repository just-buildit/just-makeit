- **One copy of the per-object CPython type section** (gh-1860). A
    standalone object's `_ext.c` and each module object's section are now
    both rendered from `templates/c/src/component_type.c`. Before this they
    were two copies that every glue fix had to change by hand in step.
    Generated output is byte-identical, and a test refuses a second copy.
