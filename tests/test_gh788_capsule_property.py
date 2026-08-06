"""gh-788 gap 4 — an object can publish a borrowed pointer as a PyCapsule.

gh-432 taught jm to **consume** a foreign C pointer arriving as a named
`PyCapsule` (or duck-unwrapped from an object's `_capsule`). Nothing could
hand one out. doppler's `Telemetry` is the attach point for every instrumented
component — it lends a borrowed `dp_tlm_t *` so their `set_telemetry` can take
it — and with no way to declare that, the whole `telemetry` module stayed
`no_generate = "true"`.

gh-286's `kind = "capsule"` is a different shape entirely: free functions over
an opaque capsule *as the state*, not a property on a `PyTypeObject`.

The cost of staying `no_generate` is why this is worth closing rather than
worked around: the module carries **three unlinked hand-written doc surfaces**
— the header's Doxygen, the `PyMethodDef` literals, and a fully hand-written
`.pyi` — with nothing rendering one from another and nothing gating them.
doppler's own `check_doc_face_parity.py` only walks sacred `_ext_<obj>.c`
fragments, so a whole-module `_ext.c` is outside its reach. That is the
gh-767/gh-777 drift-invisibility class one level up.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

CAP = "doppler.telemetry.tlm"


def _project(tmp_path: Path, **kw) -> Path:
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root)
        object_run(
            root, "telemetry", None, state_vars=[("cap", "size_t", "0")]
        )
        property_run(
            root,
            "telemetry",
            "_capsule",
            None,
            "capsule",
            False,
            capsule=CAP,
            **kw,
        )
    return root


def _ext(root: Path) -> str:
    return (
        root / "native" / "src" / "telemetry" / "telemetry_ext.c"
    ).read_text()


class TestTheGetter:
    def test_it_publishes_the_declared_capsule_name(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert f'"{CAP}"' in ext
        assert "PyCapsule_New" in ext

    def test_the_destructor_is_null(self, tmp_path):
        """The load-bearing detail. The capsule lends a pointer the object
        still owns; a capsule with a destructor would free it on garbage
        collection and the owner would free it again in `__dealloc__`. A
        double free is not a thing to leave to a default."""
        ext = _ext(_project(tmp_path))
        call = re.search(r"PyCapsule_New\((.*?)\);", ext, re.S)
        assert call, "no PyCapsule_New emitted"
        assert call.group(1).rstrip().endswith("NULL"), call.group(1)

    def test_it_defaults_to_the_handle(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert "(void *)(self->handle)" in ext

    def test_an_expr_names_the_pointer(self, tmp_path):
        """A forwarder lends something reached through the handle, not the
        handle itself — same reach `expr` gives every other property."""
        root = _project(tmp_path, expr="self->handle->inner")
        assert "(void *)(self->handle->inner)" in _ext(root)

    def test_the_destroyed_guard_is_kept(self, tmp_path):
        """Handing out a capsule over a freed handle is exactly the crash
        the guard exists to prevent, so the new branch must not skip it."""
        ext = _ext(_project(tmp_path))
        body = re.search(
            r"Telemetry_getprop__capsule.*?\n\}", ext, re.S
        ).group(0)
        assert "self->handle" in body and "destroyed" in body

    def test_it_is_wired_into_the_getset_table(self, tmp_path):
        """A getter no PyGetSetDef references is dead code that compiles and
        changes nothing — the gh-627 lesson."""
        ext = _ext(_project(tmp_path))
        assert '{ "_capsule", (getter)Telemetry_getprop__capsule' in ext


class TestTheOtherFaces:
    def test_the_stub_annotates_any(self, tmp_path):
        """A capsule has no Python type to name; `Any` is what gh-432's
        capsule-typed *params* already annotate to, so the producing and
        consuming faces of one pointer read alike."""
        pyi = (
            _project(tmp_path) / "src" / "proj" / "telemetry.pyi"
        ).read_text()
        assert "def _capsule(self) -> Any:" in pyi
        assert "Any" in pyi.split("class")[0], "Any must be imported"

    def test_no_accessor_is_declared_in_the_sacred_header(self, tmp_path):
        """Pure glue, like expr/buf_field: the getter reads a pointer it
        already has, so there is no `telemetry_get__capsule()` for the author
        to implement — declaring one would be a prototype with no definition.
        """
        h = (
            _project(tmp_path)
            / "native"
            / "inc"
            / "telemetry"
            / "telemetry_core.h"
        ).read_text()
        assert "_capsule" not in h

    def test_it_round_trips_through_the_manifest(self, tmp_path):
        root = _project(tmp_path)
        cfg = C.load(root)
        prop = next(
            p
            for p in C.properties(cfg, "telemetry")
            if p["name"] == "_capsule"
        )
        assert prop["capsule"] == CAP

    def test_apply_replays_it(self, tmp_path):
        """The manifest is the source of truth — a capsule property declared
        by hand and materialised by `apply` must render identically to one
        added by the command."""
        root = _project(tmp_path)
        before = _ext(root)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        assert f'"{CAP}"' in _ext(root)
        assert "PyCapsule_New" in _ext(root)
        assert before == _ext(root), "apply must be idempotent here"


class TestItIsNotConfusedWithAScalar:
    def test_the_type_check_does_not_reject_it(self, tmp_path, capsys):
        """`capsule` names no C type, so it must bypass the _CTYPE_META
        check the way container/expr/buf_field already do."""
        root = _project(tmp_path)
        assert root.exists()

    def test_a_bare_unknown_type_is_still_rejected(self, tmp_path, capsys):
        """Guard: the bypass must be scoped to `capsule`, not a hole."""
        root = tmp_path / "p2"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("p2", root)
            object_run(
                root, "thing", None, state_vars=[("g", "double", "1.0")]
            )
        capsys.readouterr()
        with pytest.raises(SystemExit):
            property_run(root, "thing", "x", None, "not_a_type", False)
        assert "unsupported --type" in capsys.readouterr().err


@pytest.mark.skipif(
    not __import__("shutil").which("cmake"), reason="needs a C toolchain"
)
class TestItCompiles:
    """The claim is that the module builds — the symbol being textually
    present is only evidence for it. `PyCapsule_New`'s third argument is
    typed, so a wrong destructor spelling is a compile error, not a runtime
    surprise."""

    def test_a_capsule_property_builds(self, tmp_path):
        root = _project(tmp_path)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; from just_makeit import _build;"
                "r=Path('.').resolve(); _build._ensure_built(r, r / 'build')",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=900,
            env={
                **os.environ,
                "PYTHONPATH": str(
                    Path(__file__).resolve().parent.parent / "src"
                ),
            },
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
