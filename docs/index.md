<h1 id="__skip" align="center">
  <img src="https://raw.githubusercontent.com/just-buildit/just-makeit/main/docs/assets/logo-wordmark.png" alt="just-makeit" width="540">
</h1>

[![CI](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml)
[![Docs](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml)

Getting an algorithm right is paramount.  Yet it's rarely the bottleneck.
Turning it into shippable code — a tested C library, a Python binding, a build
system, packaging, and a public C API that Rust or C++ can also link — is the
tedious, exacting work that repeats on every project.

```termynal
$ . <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
    {g}ok{/g}  Python 3.12
    {g}ok{/g}  cmake 4.2.3  (already installed)
    {g}ok{/g}  C compiler (/usr/bin/gcc)  (already installed)
  {mark}-->{/mark}   just-makeit  (/tmp/jm-venv)
{b}==>{/b} Setting up venv at /tmp/jm-venv {g}✓{/g}
    {g}ok{/g}  numpy 2.4.6
    {g}ok{/g}  just-makeit 0.19.27

{b}==> Venv activated — just-makeit is ready:{/b}

    just-new my_project --object my_object
```

`just-makeit new` scaffolds the whole thing in one command: core C library, thin
Python binding, CMake build system, and full test coverage — all passing before
you write a single line of your algorithm.

```termynal
$ just-makeit new my_project --object my_object
{d}just-makeit: creating project 'my_project'{/d}

  create  native/inc/my_object/my_object_core.h
  create  native/src/my_object/my_object_core.c
  create  native/src/my_object/my_object_ext.c
  create  native/tests/test_my_object_core.c
  create  src/my_project/my_object.pyi
  create  src/my_project/tests/test_my_object.py
  create  CMakeLists.txt  Makefile  pyproject.toml  …

{g}Done!{/g}  {c}cd my_project && make && make test{/c}
$ cd my_project && make && make test
{G}[ 27%] Building C object my_object_core.c.o{/G}
{g}[ 72%] Linking C shared library libmy_project.so{/g}
{g}[100%] Linking C shared module my_object.cpython-312.so{/g}
{b}Copy my_object extension module{/b}
[100%] Built target my_object

1/1 Test #1: test_my_object_core ....   {g}Passed{/g}    0.00 sec
{g}100% tests passed{/g}, 0 tests failed out of 1

test_create ... {g}ok{/g}
test_step_runs ... {g}ok{/g}
test_steps_shape_dtype ... {g}ok{/g}
{d}----------------------------------------------------------------------{/d}
Ran 8 tests in 0.026s
{g}OK{/g}
```

A complete, green C library **and** Python extension — C *and* Python tests
passing — before you write a line of your algorithm. Fill in `step()` and ship.

______________________________________________________________________

## Quickstart

### Get it

`install-deps` installs the build toolchain — cmake, a C compiler, and numpy —
into a Python venv, creating and activating it for you.

=== "curl"


    ```sh
    . <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
    ```

=== "pip"

    ```sh
    pip install just-makeit && just-makeit install-deps [path]
    ```

=== "uv"

    ```sh
    uv tool install just-makeit && just-makeit install-deps [path]
    ```

______________________________________________________________________

!!! note

     The venv is created at `/tmp/jm-venv` by default. To put it elsewhere,
     append the path to any of the commands above — e.g.
     `. <(curl -fsSL …/install.sh) ~/my-venv`.


!!! info

    Installer detects your platform and installs system dependencies via
    the available package manager:

    | Platform     | Detection order                                            |
    |--------------|------------------------------------------------------------|
    | **Linux**    | apt · dnf · pacman · zypper · apk                          |
    | **macOS**    | Homebrew                                                   |


______________________________________________________________________

### Get it with Docker

```sh
docker run --rm -it ghcr.io/just-buildit/jm-examples-linux:latest
```

______________________________________________________________________

!!! Tip

    **No install needed** - the container prints a welcome message with everything you need:
        
    - pre-built example projects in `~/examples/`
    - commands to browse or re-run them
    - a quickstart for your own project

______________________________________________________________________

## Next steps

| Goal | Page |
| ---- | ---- |
| Scaffold → implement → test loop | [Workflow](workflow.md) |
| All generated file layouts | [Artifacts](artifacts.md) |
| Tour every feature in one project | [Feature tour](feature-tour.md) |
| Runnable bundled examples | [Examples](examples/index.md) |
| Generated C and Python API reference | [Workflow → Generated C API](workflow.md#generated-c-api) |
| Command options | [Commands → Scaffold](commands/scaffold.md) |

______________________________________________________________________

## Requirements

- Python 3.9+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, MSVC/MinGW)
- NumPy (runtime, for generated projects)

______________________________________________________________________

## Authors

Matthew T. Hunter, Ph.D. and [Claude Code](https://claude.ai/code)
