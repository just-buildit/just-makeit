# visual_studio_pip example

A generated project built and tested the way a Visual Studio user does it on
Windows, with the project's `.venv` made by **pip**: scaffold from
PowerShell, create the venv, open the folder, pick a preset, build, run the
tests. Using uv instead? Follow
[Visual Studio on Windows (uv)](visual_studio_uv.md); the two pages differ
only in the venv step.

Every IDE action below is shown next to the command line it maps to. Visual
Studio and the command line read the same generated `CMakePresets.json`
(gh-1376), so each pair configures, builds and tests the same thing. This
example's own test runs the commands on this page, step by step, and CI runs
them as written on `windows-latest` with clang-cl.

## TL;DR -- see it work first

From a **Developer PowerShell for VS**, which loads the build environment
clang-cl needs:

```powershell
jbx just-makeit:install
jm example visual_studio_pip
# visual_studio_pip: PASSED
```

## Prerequisites

Visual Studio 2022 or later with the *Desktop development with C++*
workload, clang-cl, and an x64 Python 3.9+ with the `py` launcher. The full
list, and why the compiler is clang-cl rather than `cl.exe`, is in
[Building on Windows](../windows.md#what-you-need). Visual Studio ships
CMake and Ninja, so nothing else is needed.

jm is installed with [jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit), jm's recommended install,
the same line on every platform; step 1 runs it.
