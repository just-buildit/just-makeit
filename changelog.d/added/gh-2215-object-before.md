- **`jm object --module M --before OBJ` places an object in the module's
    order** (gh-2215). It inserts the new object ahead of `OBJ` in
    `[module.M] objects` -- the module's load order, which the root
    `CMakeLists.txt` and the umbrella header follow -- instead of appending
    it. It is refused without `--module`, or naming an object that is not
    in that module.
