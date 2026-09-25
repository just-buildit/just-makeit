- **Removing a module's last function removes its C test and bench too**
    (gh-1479). The functions were the module's core, and with the last one
    gone nothing builds it; its `test_<module>_core.c` and
    `bench_<module>_core.c` stayed behind, compiled by nothing, and `jm   status --check` failed UNBUILT on the tree `jm remove` had just produced.
    They now go as an object's do on `jm remove object`, unless an object of
    the module shares its name (then they are that object's).
