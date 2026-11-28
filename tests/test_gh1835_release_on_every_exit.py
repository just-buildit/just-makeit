"""A refused argument releases everything the binding already holds.

gh-1835 and gh-2689, two reports of one defect: a binding that takes several
arguments acquires them one after another -- a ``path`` converted by the
parse, then each array -- and a refusal part-way returns early. Each early
return owes a release of whatever is held AT THAT POINT, and each emitter
spelled that debt for itself:

* gh-1835: an ``optional`` array init-param, refused, returned without
  releasing the arrays acquired before it (one reference per bad call);
* gh-2689: a module function's array refusal released only the paths
  declared BEFORE the array, so ``(x: float[], p: path, y: float[])`` leaked
  ``p`` when ``x`` was refused;
* the method peer (``_context/_parse._build_params_parse``) released no path
  on ANY refusal -- an array, an enum, a capsule;
* a module function with an array before an enum did not compile at all:
  the enum's refusal named the array local, which the conversion runs ahead
  of (``'x_arr' undeclared``).

Now every one of them asks ``_coerce.held_release``, the one emitter of "the
arrays held, then every path", and the parse-time exits ask it for the paths
alone.

GATE. A compiled fixture with every shape, each refused 100 times with the
reference counts compared. The array is an ndarray the test holds; the path
is an ``os.PathLike`` whose ``__fspath__`` returns ONE ``bytes`` object the
test holds -- ``PyUnicode_FSConverter`` hands a bytes back as itself with a
new reference, so a path the binding forgot to release shows as a growing
count on that object.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig

import pytest
from _cmake import PYTHON3_ARG
from _compilers import default_cc
from _jmrun import run_cli

pytestmark = pytest.mark.skipif(
    shutil.which("cmake") is None or default_cc() is None,
    reason="needs cmake and a C compiler",
)

#: Every shape that holds something when it refuses an argument. `put` is
#: the issue's own: the path sits BETWEEN two arrays.
_FRAGMENT = """\
[[enum]]
name = "kind"
values = ["a", "b"]

[thing]
arg_type = "void"
return_type = "void"
no_state = "true"
no_step = "true"
no_reset = "true"
class_name = "Thing"

[[thing.init_params]]
name = "a"
type = "float[]"

[[thing.init_params]]
name = "taps"
type = "float[]"
optional = true
create_fn = "p_thing_create_taps"

[[thing.methods]]
name = "mp"
arg_type = "void"
return_type = "int"
params = [
    {name = "x", type = "float[]"},
    {name = "pth", type = "path"},
    {name = "y", type = "float[]"},
]
impl = "(void)state; (void)x; (void)pth; (void)y; return 0;"

[[thing.methods]]
name = "me"
arg_type = "void"
return_type = "int"
params = [
    {name = "x", type = "float[]"},
    {name = "pth", type = "path"},
    {name = "k", type = "int", enum = "kind"},
]
impl = "(void)state; (void)x; (void)pth; (void)k; return 0;"

[[thing.methods]]
name = "mc"
arg_type = "void"
return_type = "int"
params = [
    {name = "pth", type = "path"},
    {name = "h", type = "void *", capsule = "p.h"},
    {name = "x", type = "float[]"},
]
impl = "(void)state; (void)x; (void)pth; (void)h; return 0;"

[grid]
arg_type = "void"
return_type = "void"
no_state = "true"
no_step = "true"
no_reset = "true"
class_name = "Grid"

[[grid.init_params]]
name = "a"
type = "float[]"

[[grid.init_params]]
name = "bank"
type = "float[][]"
optional = true
create_fn = "p_grid_create_bank"

[module.m]
objects = ["mdisp"]

[mdisp]
arg_type = "void"
return_type = "void"
no_state = "true"
no_step = "true"
no_reset = "true"
class_name = "Mdisp"

[[mdisp.init_params]]
name = "a"
type = "float[]"

[[mdisp.init_params]]
name = "taps"
type = "float[]"
optional = true
create_fn = "p_mdisp_create_taps"

[module.ops]
objects = []

[[module.ops.functions]]
name = "fp"
return_type = "int"
impl = "(void)x; (void)p; (void)y; return 0;"
[[module.ops.functions.params]]
name = "x"
type = "float[]"
[[module.ops.functions.params]]
name = "p"
type = "path"
[[module.ops.functions.params]]
name = "y"
type = "float[]"

[[module.ops.functions]]
name = "fe"
return_type = "int"
impl = "(void)x; (void)p; (void)k; return 0;"
[[module.ops.functions.params]]
name = "x"
type = "float[]"
[[module.ops.functions.params]]
name = "k"
type = "int"
enum = "kind"
[[module.ops.functions.params]]
name = "p"
type = "path"

[[module.ops.functions]]
name = "fc"
return_type = "int"
impl = "(void)p; (void)h; (void)x; return 0;"
[[module.ops.functions.params]]
name = "p"
type = "path"
[[module.ops.functions.params]]
name = "h"
type = "void *"
capsule = "p.h"
[[module.ops.functions.params]]
name = "x"
type = "float[]"
"""

#: (label, call). `P()` is the held path, `x` the held array, `bad` an
#: argument no array coerces from, `badk` a string that is no choice.
CASES = [
    ("init: optional array refused", "Thing(x, taps='bad')"),
    ("init: optional 2-D array refused", "Grid(x, bank='bad')"),
    ("init: optional 2-D rank refused", "Grid(x, bank=np.zeros(2, 'f4'))"),
    ("module init: optional refused", "Mdisp(x, taps='bad')"),
    ("method: array before path refused", "t.mp(bad, P(), y)"),
    ("method: array after path refused", "t.mp(x, P(), bad)"),
    ("method: enum refused", "t.me(x, P(), 'zzz')"),
    ("method: capsule refused", "t.mc(P(), 5, x)"),
    ("method: array refused after capsule", "t.mc(P(), None, bad)"),
    ("function: array before path refused", "fp(bad, P(), y)"),
    ("function: array after path refused", "fp(x, P(), bad)"),
    ("function: enum refused", "fe(x, 'zzz', P())"),
    ("function: capsule refused", "fc(P(), 5, x)"),
    ("function: array refused after capsule", "fc(P(), None, bad)"),
]

_PROBE = """\
import sys
sys.path.insert(0, {src!r})
import numpy as np
from p.thing import Thing
from p.grid import Grid
from p.m import Mdisp
from p.ops import fp, fe, fc


class P:
    held = b"/held"

    def __fspath__(self):
        return self.held


x = np.zeros(3, np.float32)
y = np.zeros(3, np.float32)
bad = ["a", "b"]
t = Thing(x)
cases = {cases!r}
for label, call in cases:
    r0, p0 = sys.getrefcount(x), sys.getrefcount(P.held)
    raised = 0
    for _ in range(100):
        try:
            eval(call)
        except (ValueError, TypeError, AttributeError):
            raised += 1
    print(label, "|", raised, sys.getrefcount(x) - r0,
          sys.getrefcount(P.held) - p0)
"""


@pytest.fixture(scope="module")
def probed(tmp_path_factory):
    """The fixture applied, built, and every case run in a child Python:
    ``{label: (raised, array_leak, path_leak)}``."""
    base = tmp_path_factory.mktemp("gh1835")
    root = base / "p"
    for args, cwd in (
        (("new", "p", str(root), "--no-c-prefix"), base),
        (("apply", str(base / "frag.toml")), root),
    ):
        if args[0] == "apply":
            (base / "frag.toml").write_text(_FRAGMENT, encoding="utf-8")
        r = run_cli(*args, cwd=cwd)
        assert r.returncode == 0, f"{args}\n{r.stdout}\n{r.stderr}"
    build = root / "build"
    for cmd in (
        ["cmake", "-S", str(root), "-B", str(build), PYTHON3_ARG],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-4000:]
    assert sorted(root.rglob("*" + sysconfig.get_config_var("EXT_SUFFIX")))
    script = _PROBE.format(src=str(root / "src"), cases=CASES)
    r = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr[-3000:]
    got = {}
    for line in r.stdout.splitlines():
        label, _, nums = line.partition(" | ")
        raised, arr, pth = (int(n) for n in nums.split())
        got[label] = (raised, arr, pth)
    return got


@pytest.mark.slow
@pytest.mark.parametrize("label", [c[0] for c in CASES])
def test_a_refusal_releases_what_the_binding_holds(probed, label):
    raised, array_leak, path_leak = probed[label]
    assert raised == 100, f"{label}: only {raised}/100 calls were refused"
    assert array_leak == 0, f"{label}: leaked {array_leak} array refs"
    assert path_leak == 0, f"{label}: leaked {path_leak} path refs"
