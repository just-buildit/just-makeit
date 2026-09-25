"""gh-1494: the CTOR check reads the constructor the object actually binds.

An object may name its constructor (``create_fn``), and the component context
publishes that name as ``create_name``. ``_ctorsig.drift`` looked it up as
``ctx["create_fn"]`` -- a key the context never sets -- so it always fell back
to ``<comp>_create``, whatever the object declared.

Nothing hit it while every override lived beside NO ``<comp>_create``: the
declaration is then absent, ``declared_params`` returns ``None``, and the
check skips silently (doppler's ``acq``). It bites the object that keeps a
public ``<comp>_create`` of its own shape AND binds Python through another
function -- doppler's ``burst_acq`` (doppler-dsp/doppler#1479), where the
check compared the unchanged C API against the binding's parameter list and,
CTOR being unsuppressible, held ``status --check`` red on drift that was not.

The three tests pin both directions: the override is what is compared (a
differently shaped ``<comp>_create`` beside it is not a finding), the
override is still CHECKED (a drifted one is), and without an override the
check is what it was.
"""

# gh-1591: this file's hand-written C and expectations spell jm's bare
# derived names, so its projects opt out of the prefix `jm new` now
# defaults to; the default is gated by tests/test_gh1591_*.py.

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _ctorsig  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_RENDERED = "const float *h, size_t h_len"


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path, no_state: bool = False) -> Path:
    root = tmp_path / "d1494"
    _quiet(new_run, "d1494", root, c_prefix=None)
    _quiet(module_run, root, "dsp")
    _quiet(
        object_run,
        root,
        "hb",
        module="dsp",
        arg_type="float",
        return_type="float",
        init_params=[("h", "float[]", "")],
        no_state=no_state,
        no_step=no_state,
    )
    return root


# Both object shapes: a stateful and a `no_state` object build their context
# through different branches, and the override must reach the check through
# both. (doppler's burst_acq, the case that found this, is `no_state`.)
_SHAPES = pytest.mark.parametrize(
    "no_state", [False, True], ids=["stateful", "no_state"]
)


def _header(root: Path) -> Path:
    return root / INC_ROOT / "hb" / "hb_core.h"


def _with_override(root: Path, override_params: str) -> dict:
    """Keep ``hb_create`` as a DIFFERENT public shape, add the override the
    binding calls, and name it as the object's ``create_fn``."""
    h = _header(root)
    text = h.read_text(encoding="utf-8")
    old = f"hb_state_t *hb_create({_RENDERED});"
    assert old in text, text
    text = text.replace(
        old,
        "hb_state_t *hb_create(size_t h_len, const float *h, int mode);\n"
        f"hb_state_t *hb_bind({override_params});",
        1,
    )
    h.write_text(text, encoding="utf-8")
    cfg = C.load(root)
    cfg["hb"]["create_fn"] = "hb_bind"
    return cfg


@_SHAPES
def test_a_public_create_of_another_shape_is_not_drift(tmp_path, no_state):
    """The override matches the manifest; ``hb_create`` is a C API with its
    own parameters. Nothing is stale, so nothing may fire."""
    root = _project(tmp_path, no_state)
    cfg = _with_override(root, _RENDERED)
    assert _ctorsig.drift(root, cfg) == []


@_SHAPES
def test_a_drifted_override_is_still_a_finding(tmp_path, no_state):
    """The fix must redirect the check, not switch it off: the override
    the binding calls is compared, and a reordered one is caught."""
    root = _project(tmp_path, no_state)
    cfg = _with_override(root, "size_t h_len, const float *h")
    (d,) = _ctorsig.drift(root, cfg)
    assert d.component == "hb"
    assert d.declared == "size_t h_len, const float *h"
    assert d.rendered == _RENDERED


@_SHAPES
def test_without_an_override_the_check_is_unchanged(tmp_path, no_state):
    """No ``create_fn``: ``<comp>_create`` is the constructor, as before."""
    root = _project(tmp_path, no_state)
    assert _ctorsig.drift(root, C.load(root)) == []
    h = _header(root)
    h.write_text(
        h.read_text(encoding="utf-8").replace(
            f"hb_create({_RENDERED});",
            "hb_create(size_t h_len, const float *h);",
            1,
        ),
        encoding="utf-8",
    )
    (d,) = _ctorsig.drift(root, C.load(root))
    assert d.declared == "size_t h_len, const float *h"
