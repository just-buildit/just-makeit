- **A configure whose `.venv` does not exist says so** (gh-2610). The
    presets name the project's `.venv` interpreter, and Visual Studio
    configures as soon as the folder is opened, which can be before the
    `.venv` exists. That failed as CMake's bare `Could NOT find Python3`, and
    Visual Studio kept the failed cache. The root `CMakeLists.txt` now stops
    first when `Python3_EXECUTABLE` names a file that is not there. When
    that file is in the project's own `.venv`, it says the `.venv` is
    missing, gives the pip and the `uv sync` way to make it, and says to
    reconfigure (*Project > Delete Cache and Reconfigure*); any other path,
    such as one a `CMakeUserPresets.json` sets, it names, with where it can
    be set.
    An existing project's root file is the author's outside jm's blocks, so
    `jm status` reports it as `ROOT CMAKE venv-check` until the check is
    merged in.
