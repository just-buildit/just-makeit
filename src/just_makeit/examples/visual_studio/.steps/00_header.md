# visual_studio example

A generated project built and tested the way a Visual Studio user does it on
Windows: scaffold from PowerShell, open the folder, pick a preset, build, run
the tests.

Every IDE action below is shown next to the command line it maps to. Visual
Studio and the command line read the same generated `CMakePresets.json`
(gh-1376), so each pair configures, builds and tests the same thing. This
example's own test runs the commands on this page, step by step, and CI runs
it on `windows-latest` with clang-cl.

## TL;DR -- see it work first

From a **Developer PowerShell for VS**, which loads the build environment
clang-cl needs:

```powershell
py -m pip install just-makeit
jm example visual_studio
# visual_studio: PASSED
```

## Prerequisites

Visual Studio 2022 or later with the *Desktop development with C++*
workload, clang-cl, and an x64 Python 3.9+. The full list, and why the
compiler is clang-cl rather than `cl.exe`, is in
[Building on Windows](../windows.md#what-you-need).

Install just-makeit into that Python, with pip or with uv:

```powershell
py -m pip install just-makeit
uv tool install just-makeit
```

Visual Studio ships CMake and Ninja, so nothing else is needed.
