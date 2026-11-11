- **The generated `CMakeLists.txt` explains each step in plain terms.**
    Its comments were a record of the issue behind each line: why a flag
    exists, which bug it closed, the toolchain it was measured on. They
    now say what each step does for the project -- the build type, the
    Windows settings, the Python check, the C library, each part of the
    install -- in a sentence or two, with no issue numbers. Each
    component's `CMakeLists.txt` labels its extension module, C test and
    benchmark, and no longer names `libmy_dsp.so`, a leftover project
    name. The libm lookup moves up with the project-wide settings. Only
    comments and placement change; what the project builds and installs is
    the same.
