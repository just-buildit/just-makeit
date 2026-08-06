"""gh-790 — an object can be CONSTRUCTED from another module's capsule.

The third and last side of the capsule triangle. gh-432 taught jm to *consume*
a foreign C pointer as a **method param**; gh-788 gap 4 taught it to *produce*
one as a property. What was missing is *constructing* an object around a handle
another module owns — the generic "layer one object over another module's
handle" shape (a capture over a telemetry context, a sink over a stream
context, a view over a plan), which kept every such object hand-written even
when the rest of it was fully declarative.

Two things make this more than another param kind:

**The type is not one jm knows.** `dp_tlm_t *` is the pointer's own spelling —
deliberately absent from `_CTYPE_META`, because jm's entire involvement is
passing it along. So the capsule branch has to be tested *before* every
classifier that would otherwise `KeyError` on it, and the `header` that
declares it has to reach the sacred `_core.h` — the foreign type lands in the
`<comp>_create()` prototype, so without the include the header does not parse.

**The pointer is borrowed.** The object must hold a strong reference to the
Python owner for its own lifetime or the capsule dangles the moment the
producer is collected — a use-after-free with nothing at the crash site naming
the cause. That is the half most easily forgotten by hand, and the argument for
generating it.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._cli_parse import parse_init_param_flag  # noqa: E402
from just_makeit._context._parse import capsule_unwrap_c  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

CAP = "doppler.telemetry.tlm"
PTR = "dp_tlm_t *"
HDR = "telemetry/telemetry.h"

# The foreign module's header. jm never writes this — it is the author's, the
# same way `--single`'s record struct is.
FOREIGN_H = """#ifndef TELEMETRY_H
#define TELEMETRY_H
#include <stddef.h>
typedef struct { size_t magic; } dp_tlm_t;
#endif
"""


def _project(tmp_path: Path, *, with_header: bool = True) -> Path:
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root)
        if with_header:
            d = root / "native" / "inc" / "telemetry"
            d.mkdir(parents=True)
            (d / "telemetry.h").write_text(FOREIGN_H)
        object_run(
            root,
            "capture",
            None,
            state_vars=[("seen", "size_t", "0")],
            init_params=[
                (
                    "tlm",
                    PTR,
                    "",
                    "",
                    "",
                    "",
                    False,
                    "",
                    True,
                    "",
                    CAP,
                    HDR,
                ),
                ("block_samples", "size_t", "0"),
            ],
        )
    return root


def _ext(root: Path) -> str:
    return (root / "native" / "src" / "capture" / "capture_ext.c").read_text()


def _core_h(root: Path) -> str:
    return (root / "native" / "inc" / "capture" / "capture_core.h").read_text()


def _pyi(root: Path) -> str:
    return (root / "src" / "proj" / "capture.pyi").read_text()


class TestTheCFace:
    def test_create_takes_the_plain_pointer(self, tmp_path):
        """The capsule is Python-side transport only. C sees the pointer the
        author would have written by hand, so the C smoke test and any other
        C caller can use `create()` directly."""
        assert (
            f"capture_state_t *capture_create({PTR}tlm, size_t"
            " block_samples);" in _core_h(_project(tmp_path))
        )

    def test_the_foreign_header_is_included(self, tmp_path):
        """Without it the sacred `_core.h` does not parse — `dp_tlm_t` is
        undeclared in the very prototype jm just wrote."""
        assert f'#include "{HDR}"' in _core_h(_project(tmp_path))

    def test_the_include_lands_at_CREATION_not_only_at_apply(self, tmp_path):
        """The bug this guards. `param_headers` reads the manifest, and at
        object-creation the component is not in it yet (run() persists at the
        end). A method's capsule header never hit this, because a method is
        added to an object that already exists — an init-param arrives WITH
        the object. Before the fix the header only appeared once someone
        happened to run `jm apply`."""
        root = _project(tmp_path)
        # No apply_run() above — this is the creation-time render.
        assert f'#include "{HDR}"' in _core_h(root)

    def test_a_missing_header_is_not_included(self, tmp_path):
        """Pre-existing gh-432 rule, kept: jm does not emit an include for a
        file that is not there."""
        root = _project(tmp_path, with_header=False)
        assert f'#include "{HDR}"' not in _core_h(root)


class TestTheBinding:
    def test_it_parses_as_an_object(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert 'kwlist[] = {"tlm", "block_samples", NULL}' in ext
        assert "PyObject *tlm_obj = NULL;" in ext
        # "O" for the capsule, then "|K" for the defaulted size_t.
        assert '"O|K", kwlist' in ext

    def test_it_name_checks_the_capsule(self, tmp_path):
        """The check that stops one module's pointer being accepted by
        another."""
        ext = _ext(_project(tmp_path))
        assert f'PyCapsule_GetPointer(tlm_cap, "{CAP}")' in ext

    def test_it_accepts_the_duck_typed_wrapper(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert 'PyObject_GetAttrString(tlm_obj, "_capsule")' in ext

    def test_the_pointer_reaches_create(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert "capture_create(tlm, block_samples)" in ext

    def test_it_is_required(self, tmp_path):
        """A handle has no zero value jm could invent, so — like `path` and
        `bytes` — it is always a required positional, and None is rejected
        rather than passed through as NULL."""
        ext = _ext(_project(tmp_path))
        assert "tlm_obj == Py_None" in ext
        assert "PyExc_TypeError" in ext

    def test_a_default_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="cannot declare a default"):
            with contextlib.redirect_stdout(io.StringIO()):
                root = tmp_path / "p2"
                new_run("p2", root)
                object_run(
                    root,
                    "c2",
                    None,
                    state_vars=[("n", "size_t", "0")],
                    init_params=[
                        (
                            "tlm",
                            PTR,
                            "NULL",
                            "",
                            "",
                            "",
                            False,
                            "",
                            True,
                            "",
                            CAP,
                            HDR,
                        )
                    ],
                )


class TestOwnership:
    """The borrowed pointer must not outlive its owner."""

    def test_the_struct_holds_the_owner(self, tmp_path):
        assert "PyObject *_tlm_owner;" in _ext(_project(tmp_path))

    def test_init_takes_a_strong_reference(self, tmp_path):
        ext = _ext(_project(tmp_path))
        assert "Py_INCREF(tlm_obj);" in ext
        # Py_XSETREF, not a bare store: __init__ is callable twice on one
        # object and the second call would otherwise leak the first owner.
        assert "Py_XSETREF(self->_tlm_owner, tlm_obj);" in ext

    def test_dealloc_releases_it(self, tmp_path):
        assert "Py_XDECREF(self->_tlm_owner);" in _ext(_project(tmp_path))

    def test_the_owner_field_survives_a_method(self, tmp_path):
        """The trap. `make_methods_ctx` runs after `make_state_ctx` and
        REPLACES `extra_buf_fields` wholesale, so an owner field emitted into
        that slot would vanish the moment the object also declared a method —
        leaving a tp_init that stores into a member the struct never declared.
        Hence the dedicated slot; this is what proves it."""
        from just_makeit._method import run as method_run

        root = _project(tmp_path)
        with contextlib.redirect_stdout(io.StringIO()):
            method_run(
                root, "capture", "drain", None, "void", "float", True, []
            )
        ext = _ext(root)
        assert "PyObject *_tlm_owner;" in ext
        assert "Py_XDECREF(self->_tlm_owner);" in ext


class TestThePythonFace:
    def test_the_stub_annotates_object(self, tmp_path):
        """`object`, not `Any`: the binding accepts the capsule OR anything
        exposing `._capsule`, neither of which is nameable — but `Any` would
        type-check the int a reader might otherwise try."""
        assert (
            "def __init__(self, tlm: object, block_samples: int = 0)"
            in _pyi(_project(tmp_path))
        )

    def test_the_ctor_is_unseedable(self, tmp_path):
        """jm cannot conjure a foreign handle, so the generated example and
        smoke-test seeding must not pretend to — same treatment `path` and
        `bytes` already get."""
        h = _core_h(_project(tmp_path))
        assert "capture_create(NULL, 0)" in h


class TestTheManifest:
    def test_it_round_trips(self, tmp_path):
        ip = C.init_params(C.load(_project(tmp_path)), "capture")
        tlm = ip[0]
        assert tlm[0] == "tlm" and tlm[1] == PTR
        assert tlm[10] == CAP
        assert tlm[11] == HDR

    def test_apply_is_idempotent(self, tmp_path):
        root = _project(tmp_path)
        before = _ext(root)
        with contextlib.redirect_stdout(io.StringIO()):
            apply_run(root)
        assert before == _ext(root)

    def test_script_reconstructs_the_flag(self, tmp_path):
        import just_makeit._script as S

        root = _project(tmp_path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S.run(root)
        assert f"tlm:{PTR}:capsule:{CAP}:{HDR}" in buf.getvalue()


class TestTheCliGrammar:
    def test_it_parses(self):
        p, nxt = parse_init_param_flag(
            ["--init-param", f"tlm:{PTR}:capsule:{CAP}:{HDR}"], 0
        )
        assert nxt == 2
        assert p[0] == "tlm" and p[1] == PTR
        assert p[8] is True, "a capsule param is always required"
        assert p[10] == CAP and p[11] == HDR

    def test_the_header_is_optional(self):
        p, _ = parse_init_param_flag(
            ["--init-param", f"tlm:{PTR}:capsule:{CAP}"], 0
        )
        assert p[10] == CAP and p[11] == ""

    def test_a_missing_capsule_name_is_rejected(self, capsys):
        with pytest.raises(SystemExit):
            parse_init_param_flag(["--init-param", f"tlm:{PTR}:capsule"], 0)
        assert "needs the capsule name" in capsys.readouterr().err

    def test_an_array_is_rejected(self, capsys):
        with pytest.raises(SystemExit):
            parse_init_param_flag(
                ["--init-param", f"b:float[]:capsule:{CAP}"], 0
            )
        assert "not valid for an array" in capsys.readouterr().err

    def test_the_unknown_type_check_is_bypassed(self):
        """`dp_tlm_t *` is not in jm's type table and must not be validated
        against it — the guard has to precede that check, not follow it."""
        p, _ = parse_init_param_flag(
            ["--init-param", f"tlm:{PTR}:capsule:{CAP}"], 0
        )
        assert p[1] == PTR


class TestTheSharedEmitter:
    """One unwrap, two callers. gh-432's method path and gh-790's tp_init
    differ only in how they fail and whether None is allowed; two copies of a
    name-checked GetPointer plus its duck-typed fallback is exactly the pair
    that drifts."""

    def test_the_method_form_returns_null_and_allows_none(self):
        c = capsule_unwrap_c("p", PTR, CAP, "p_obj", "return NULL;")
        assert "return NULL;" in c
        assert "if (p_obj != Py_None) {" in c
        assert "return -1;" not in c

    def test_the_init_form_returns_minus_one_and_rejects_none(self):
        c = capsule_unwrap_c(
            "p", PTR, CAP, "p_obj", "return -1;", allow_none=False
        )
        assert "return -1;" in c
        assert "p_obj == Py_None" in c
        assert "return NULL;" not in c

    def test_only_the_init_form_upgrades_the_attribute_error(self):
        """A missing `_capsule` surfaces as AttributeError from
        GetAttrString. On a method that is gh-432's long-standing behaviour
        and is left byte-identical; on a CONSTRUCTOR it is the first thing a
        caller hits after passing the wrong object, and naming an
        implementation detail there is unhelpful."""
        method = capsule_unwrap_c("p", PTR, CAP, "p_obj", "return NULL;")
        init = capsule_unwrap_c(
            "p", PTR, CAP, "p_obj", "return -1;", allow_none=False
        )
        assert "PyErr_Clear()" not in method
        assert "PyErr_Clear()" in init
        assert "Py_TYPE(p_obj)->tp_name" in init


# ── the claims only a compiler and a live interpreter can settle ────────────


@pytest.mark.skipif(not shutil.which("cmake"), reason="needs a C toolchain")
class TestItCompilesAndRuns:
    """Producer and consumer together — gh-788 gap 4 publishing the handle,
    gh-790 constructing around it. That pair IS doppler's shape."""

    def _duo(self, tmp_path: Path) -> Path:
        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            d = root / "native" / "inc" / "telemetry"
            d.mkdir(parents=True)
            (d / "telemetry.h").write_text(FOREIGN_H)
            # Producer: its state struct is layout-compatible with dp_tlm_t,
            # so reading `tlm->magic` on the far side proves the pointer
            # really crossed rather than merely being non-NULL.
            object_run(
                root, "telemetry", None, state_vars=[("magic", "size_t", "0")]
            )
            property_run(
                root,
                "telemetry",
                "_capsule",
                None,
                "capsule",
                False,
                capsule=CAP,
            )
            object_run(
                root,
                "capture",
                None,
                state_vars=[("seen", "size_t", "0")],
                init_params=[
                    (
                        "tlm",
                        PTR,
                        "",
                        "",
                        "",
                        "",
                        False,
                        "",
                        True,
                        "",
                        CAP,
                        HDR,
                    ),
                    ("block_samples", "size_t", "0"),
                ],
            )
        core = root / "native" / "src" / "capture" / "capture_core.c"
        s = core.read_text()
        old = f"capture_create({PTR}tlm, size_t block_samples)\n{{"
        assert old in s, s[:1200]
        s = s.replace(
            old + "\n    capture_state_t *obj = calloc(1, sizeof(*obj));",
            old
            + "\n    (void)block_samples;"
            + "\n    capture_state_t *obj = calloc(1, sizeof(*obj));",
            1,
        )
        s = s.replace(
            "    obj->seen = 0;", "    obj->seen = tlm ? tlm->magic : 0;", 1
        )
        core.write_text(s)
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
        return root

    def _run(self, root: Path, body: str) -> str:
        proc = subprocess.run(
            [sys.executable, "-c", body],
            cwd=root / "src",
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc.stdout

    def test_the_pointer_crosses_both_ways_in(self, tmp_path):
        out = self._run(
            self._duo(tmp_path),
            "from proj import Telemetry, Capture\n"
            "t = Telemetry(magic=12345)\n"
            "print(Capture(t).get_seen())\n"  # duck-typed wrapper
            "print(Capture(t._capsule).get_seen())\n",  # raw capsule
        )
        assert out.split() == ["12345", "12345"]

    def test_the_owner_is_kept_alive(self, tmp_path):
        """The correctness point. Every Python reference to the producer is
        dropped and the GC run; without the generated strong reference the
        handle is freed and this read is a use-after-free."""
        out = self._run(
            self._duo(tmp_path),
            "import gc\n"
            "from proj import Telemetry, Capture\n"
            "t = Telemetry(magic=777)\n"
            "c = Capture(t)\n"
            "del t; gc.collect()\n"
            "print(c.get_seen())\n",
        )
        assert out.strip() == "777"

    def test_the_reference_is_released_not_leaked(self, tmp_path):
        out = self._run(
            self._duo(tmp_path),
            "import gc, sys\n"
            "from proj import Telemetry, Capture\n"
            "t = Telemetry(magic=1)\n"
            "before = sys.getrefcount(t)\n"
            "c = Capture(t)\n"
            "held = sys.getrefcount(t)\n"
            "del c; gc.collect()\n"
            "print(held - before, sys.getrefcount(t) - before)\n",
        )
        # +1 while the consumer lives, back to 0 once it is freed.
        assert out.strip() == "1 0"

    def test_a_wrong_capsule_name_is_rejected(self, tmp_path):
        out = self._run(
            self._duo(tmp_path),
            "import ctypes\n"
            "from proj import Capture\n"
            "f = ctypes.pythonapi.PyCapsule_New\n"
            "f.restype = ctypes.py_object\n"
            "f.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]"
            "\n"
            "wrong = f(ctypes.c_void_p(0x1234), b'some.other.name', None)\n"
            "try:\n"
            "    Capture(wrong); print('ACCEPTED')\n"
            "except ValueError:\n"
            "    print('rejected')\n",
        )
        assert out.strip() == "rejected"

    def test_the_wrong_kind_of_object_says_what_to_pass(self, tmp_path):
        out = self._run(
            self._duo(tmp_path),
            "from proj import Capture\n"
            "for a in (None, 42, 'x', object()):\n"
            "    try:\n"
            "        Capture(a); print('ACCEPTED')\n"
            "    except TypeError as e:\n"
            "        print('TypeError', 'capsule' in str(e))\n",
        )
        assert out.split("\n")[:4] == ["TypeError True"] * 4
