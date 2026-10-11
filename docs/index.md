<h1 id="__skip" align="center">
  <img src="https://raw.githubusercontent.com/just-buildit/just-makeit/main/docs/assets/logo-wordmark.png" alt="just-makeit" width="540">
</h1>

[![CI](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/ci.yml)
[![Docs](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml/badge.svg)](https://github.com/just-buildit/just-makeit/actions/workflows/docs.yml)

The algorithm is the interesting part. The scaffolding around it — C library,
Python bindings, CMake, tests, and packaging — is the same boilerplate every
project needs and nobody wants to write again.

<div class="jm-card" markdown>

**<code class="jm-green">just-makeit new</code>**{ .jm-card-title }

Generates everything, tested and passing, so you can start on the algorithm immediately.

```termynal
$ just-makeit new my_project --object my_object
{d}just-makeit: creating project 'my_project'{/d}

  create  native/inc/my_project/my_object/my_object_core.h
  create  native/src/my_object/my_object_core.c
  create  native/src/my_object/my_object_ext.c
  create  native/tests/test_my_object_core.c
  create  src/my_project/my_object.pyi
  create  src/my_project/tests/test_my_object.py
  create  CMakeLists.txt  Makefile  pyproject.toml  …

{g}Done!{/g}  {c}cd my_project && make && make test{/c}

$ cd my_project && make && make test
{G}[ 27%] Building C object ...core.c.o{/G}
{g}[ 72%] Linking C shared library ...{/g}
{g}[100%] Linking C shared module my_object.so{/g}
[100%] Built target my_object

1/1 Test #1: test_my_object_core ... {g}Passed{/g}  0.00 sec
{g}100% tests passed{/g}, 0 tests failed out of 1

test_create ... {g}ok{/g}
test_step_runs ... {g}ok{/g}
test_steps_shape_dtype ... {g}ok{/g}
{d}--------------------------------------------{/d}
Ran 8 tests in 0.026s
{g}OK{/g}
```

A complete C library and Python extension — both tested and passing — before
you write a line of your algorithm. Fill in `step()` and ship.

</div>

## Get it

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

```termynal
$ . <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
    {g}ok{/g}  Python 3.12
    {g}ok{/g}  cmake 4.2.3  (already installed)
    {g}ok{/g}  C compiler (/usr/bin/gcc)  (already installed)
    {g}ok{/g}  pkg-config (/usr/bin/pkg-config)  (already installed)
    {g}ok{/g}  patchelf (/usr/bin/patchelf)  (already installed)
  {y}-->{/y}   just-makeit  (/tmp/jm-venv)
{b}==>{/b} Setting up venv at /tmp/jm-venv {g}✓{/g}
    {g}ok{/g}  numpy 2.4.6
    {g}ok{/g}  just-makeit {jm_version}

{b}==> Venv activated — just-makeit is ready:{/b}

    just-makeit new my_project --object my_object
```

!!! note

    `install-deps` installs cmake, a C compiler, pkg-config and, on Linux,
    patchelf (auditwheel needs it to repair a wheel) through your system
    package manager (only when one of them is missing),
    then numpy and just-makeit into a Python venv. The curl installer does the
    same, and also activates the venv in your shell; `just-makeit install-deps`
    prints the `source <venv>/bin/activate` line to run. The venv is created at
    `/tmp/jm-venv` by default. To put it elsewhere, append the path to any of the
    commands above — e.g. `. <(curl -fsSL …/install.sh) ~/my-venv`. Both take
    `--check` to report what is missing without installing anything; the curl
    installer also takes `--force` to reinstall just-makeit.

!!! info

    The installer picks the package manager from your platform — on Linux,
    from the `ID` in `/etc/os-release`:

    | Platform  | Package manager | Distros (`ID`)                         |
    | --------- | --------------- | -------------------------------------- |
    | **Linux** | apt             | ubuntu, debian, linuxmint, pop         |
    | **Linux** | dnf             | fedora, rhel, centos, rocky, almalinux |
    | **Linux** | pacman          | arch, manjaro, endeavouros             |
    | **Linux** | zypper          | opensuse\*, sles                       |
    | **Linux** | apk             | alpine                                 |
    | **macOS** | Homebrew        | —                                      |

    On any other distro it installs no system packages and names the missing
    ones for you to install yourself.

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

| Goal                                 | Page                                                      |
| ------------------------------------ | --------------------------------------------------------- |
| Scaffold → implement → test loop     | [Workflows](workflows/index.md)                                   |
| All generated file layouts           | [Artifacts](artifacts.md)                                 |
| Tour every feature in one project    | [Feature tour](feature-tour.md)                           |
| Runnable bundled examples            | [Examples](examples/index.md)                             |
| Generated C and Python API reference | [Workflows → Project layout & API](workflows/layout-and-api.md#generated-c-api) |
| Command options                      | [Commands → Scaffold](commands/scaffold.md)               |

______________________________________________________________________

## Requirements

- Python 3.9+
- CMake ≥ 3.16
- A C99 compiler: GCC or Clang, or on Windows **clang-cl** — see
    [Does it work on Windows?](faq.md#does-it-work-on-windows)
- NumPy (runtime, for generated projects)

______________________________________________________________________

## Authors

Matthew T. Hunter, Ph.D. and [Claude Code](https://claude.ai/code)
