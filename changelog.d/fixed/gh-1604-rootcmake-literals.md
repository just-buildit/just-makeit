- **`apply` delivers an install-block change that follows a `#` inside a
    bracket argument or a multi-line string** (gh-1604). jm read the root
    `CMakeLists.txt` with a comment stripper that knew neither bracket
    arguments (`[[ ... ]]`) nor quoted strings spanning lines, so a `#` in
    either dropped the rest of its line. The managed install block's
    `install(CODE [[ ... ]])` is one such argument, so a template change
    after a `#` there compared equal and never reached an existing project.
    The `ROOT CMAKE` rows read the file the same way and gain the same fix.
