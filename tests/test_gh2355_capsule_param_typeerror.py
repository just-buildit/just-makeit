"""gh-2355: a capsule param given a wrong object raises TypeError.

Every capsule-typed param unwraps by duck typing: the capsule itself, or any
object exposing it as `_capsule`. An object that is neither leaked the
attribute lookup's error -- `AttributeError: 'int' object has no attribute
'_capsule'` -- which names an implementation detail instead of the
requirement, and a wrong-type argument is a `TypeError` everywhere else in
the generated bindings. A constructor already explained itself (gh-790); a
METHOD kept the raw error "byte for byte" (gh-432).

`capsule_unwrap_c` is the one emitter all three consumers share (a method
param, a constructor param, a composer's owned pointer), and the switch that
let a method keep the raw error, `explain_type_error`, had no False left once
the method flipped, so it was removed rather than left as a flag with one
value. The gates: the emitter, the rendered binding of a method, a source
gate that the switch does not come back, and the compiled probe in
`test_gh2346_composer_capsule.py` (the gh-2346 composer capsule, a real
consumer method).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._context._parse import capsule_unwrap_c  # noqa: E402

PTR = "dp_tlm_t *"
CAP = "doppler.telemetry.tlm"


@pytest.mark.parametrize("none", [True, False])
@pytest.mark.parametrize("fail", ["return NULL;", "return -1;"])
def test_every_form_names_the_param_and_the_capsule(fail, none):
    c = capsule_unwrap_c("scene", PTR, CAP, "scene_obj", fail, allow_none=none)
    assert "PyErr_Format(PyExc_TypeError" in c
    assert f"scene must be the {CAP} capsule" in c
    assert "Py_TYPE(scene_obj)->tp_name" in c


def test_only_an_attribute_error_is_upgraded():
    """gh-794: a closed handle's `_capsule` raises RuntimeError("<T> is
    closed"), the diagnosis the caller needs; it must not be reported as 'not
    a <T>'. The upgrade is guarded on the exception class."""
    c = capsule_unwrap_c("p", PTR, CAP, "p_obj", "return NULL;")
    guard = c.index("PyErr_ExceptionMatches(PyExc_AttributeError)")
    assert guard < c.index("PyErr_Clear()"), c


def test_the_switch_does_not_come_back():
    """A flag with one value is a second question nobody is asking."""
    params = inspect.signature(capsule_unwrap_c).parameters
    assert "explain_type_error" not in params, list(params)
    stray = [
        str(p.relative_to(SRC))
        for p in sorted(SRC.rglob("*.py"))
        if "explain_type_error=" in p.read_text(encoding="utf-8")
    ]
    assert not stray, stray


def test_a_methods_rendered_binding_raises_type_error(tmp_path):
    """The generated method wrapper, not only the emitter: a capsule-typed
    method param's unwrap names what to pass."""
    root = tmp_path
    r = run_cli("new", "q", "--no-c-prefix", cwd=root)
    assert r.returncode == 0, (r.stdout, r.stderr)
    q = root / "q"
    r = run_cli("object", "w", "--state", "k:int:0", cwd=q)
    assert r.returncode == 0, (r.stdout, r.stderr)
    frag = q / "objects" / "w.toml"
    target = frag if frag.exists() else q / "just-makeit.toml"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\n[[w.methods]]\nname = "attach"\nreturn_type = "int"\n'
        'params = [{ name = "scene", type = "foo_state_t *", '
        'capsule = "q.foo", header = "q/foo/foo_core.h" }]\n',
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=q)
    assert r.returncode == 0, (r.stdout, r.stderr)
    ext = (q / "native/src/w/w_ext.c").read_text(encoding="utf-8")
    wrapper = ext[ext.index("W_attach") :]
    assert "scene must be the q.foo capsule" in wrapper, wrapper[:1800]
    assert "Py_TYPE(scene_obj)->tp_name" in wrapper, wrapper[:1800]


def test_a_constructor_param_raises_type_error(tmp_path):
    """The second consumer of the shared emitter: an object's `tp_init`."""
    r = run_cli("new", "q", "--no-c-prefix", cwd=tmp_path)
    assert r.returncode == 0, (r.stdout, r.stderr)
    q = tmp_path / "q"
    r = run_cli(
        "object", "c", "--init-param", f"tlm:{PTR}:capsule:{CAP}", cwd=q
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    ext = (q / "native/src/c/c_ext.c").read_text(encoding="utf-8")
    assert f"tlm must be the {CAP} capsule" in ext, ext[:1500]


def test_a_composers_owned_pointer_raises_type_error():
    """The third: a composer source's owned-pointer field, set from a
    capsule (gh-1711)."""
    from test_gh1711_composer_owned_pointer import FRAME, _all_c, _with

    c = _all_c(_with(**FRAME))
    assert f"frame must be the {FRAME['capsule']} capsule" in c, c[:1500]
