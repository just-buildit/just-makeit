"""gh-1679: an inline step body may call any function its header declares.

The step is ``static inline`` in the sacred ``_core.h``, so a function its
body calls must be declared ABOVE it. jm declared the accessors and methods
below it -- the template put their slots after ``step_impl_def``, and
``_inject_decls_into_core_h`` (the path `jm property` / `jm method` take)
inserted before the ``extern "C"`` close -- so an ``impl`` calling
``lo_get_gain(state)`` was an implicit declaration, then conflicting types.
A header-only component had it twice over: its definitions all land in
``inline_core``, below the step, with no declaration at all.

Fixed at both writers: the template's declaration slots precede the step,
and every later insertion goes before the step's definition
(`_init.step_def_start`). A header-only component declares each definition
above the step as a ``static inline`` prototype (`family_declarations`) --
a non-static one ahead of a ``static inline`` definition is the error
gh-1321 refuses; a static one is plain C99.

GATE: a step body calling a property getter and a method builds and passes
      ctest, for a plain and a header-only component, whether the manifest
      declares them up front (rendered from the template) or `jm property` /
      `jm method` add them later (injected).
"""

from __future__ import annotations

import subprocess

import pytest

from _jmrun import run_cli
from just_makeit import _textio
from just_makeit._new import run as new_run

#: Calls the getter, and takes the method's address -- a use that needs its
#: declaration, whatever the method's constness.
IMPL = "return lo_get_gain(state) * x + 0.0f * (float)sizeof(&lo_half);"

MANIFEST = """[lo]
arg_type = "float"
return_type = "float"
{header_only}impl = "{impl}"

[[lo.state]]
name = "gain"
type = "float"
default = "1.0"

[[lo.properties]]
name = "gain"
type = "float"

[[lo.methods]]
name = "half"
arg_type = "float"
return_type = "float"
"""


def _build(root):
    steps = []
    for cmd in (
        ["cmake", "-S", ".", "-B", "b", "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", "b"],
        ["ctest", "--test-dir", "b", "--output-on-failure"],
    ):
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        steps.append((cmd[:2], r.returncode, (r.stdout + r.stderr)[-3000:]))
        if r.returncode:
            break
    return steps


def _manifest(root, header_only):
    (root / "objects").mkdir(exist_ok=True)
    _textio.write_text(
        root / "objects" / "lo.toml",
        MANIFEST.format(
            header_only='header_only = "true"\n' if header_only else "",
            impl=IMPL,
        ),
    )
    return [run_cli("apply", cwd=root)]


def _cli(root, header_only):
    ran = [
        run_cli(
            "object",
            "lo",
            "--state",
            "gain:float:1.0",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            *(["--header-only"] if header_only else []),
            cwd=root,
        ),
        run_cli("property", "lo", "gain", "--type", "float", cwd=root),
        run_cli(
            "method",
            "lo",
            "half",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=root,
        ),
    ]
    frag = root / "objects" / "lo.toml"
    text = frag.read_text()
    _textio.write_text(
        frag, text.replace("[lo]\n", f'[lo]\nimpl = "{IMPL}"\n', 1)
    )
    return ran + [run_cli("apply", cwd=root)]


@pytest.mark.parametrize(
    "header_only", [False, True], ids=["plain", "header-only"]
)
@pytest.mark.parametrize("path", [_manifest, _cli], ids=["manifest", "cli"])
def test_a_step_calling_a_getter_and_a_method_builds(
    tmp_path, path, header_only
):
    root = tmp_path / "q"
    new_run("q", root, c_prefix=None, fragments=True)
    for r in path(root, header_only):
        assert r.returncode == 0, r.stdout + r.stderr
    h = (root / "native/inc/q/lo/lo_core.h").read_text()
    assert "lo_get_gain(state) * x" in h, h
    for what, rc, out in _build(root):
        assert rc == 0, (what, out)
    # Every declaration precedes the step that may call it.
    step = h.index("lo_step(const lo_state_t")
    for fn in ("lo_get_gain(", "lo_half("):
        assert h.index(fn) < step, (fn, h)
