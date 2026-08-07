"""gh-805 §H: a capsule init-param whose NULL means something.

gh-432 (method params) and gh-790 (constructor params) are the same idea and
disagreed about `None`: the method path mapped it to `NULL`, the constructor
path rejected it unconditionally. doppler's capture borrows a
`dp_sample_clock_t *` whose `NULL` *means* "no time base stated" — after which
the sidecar omits the keys rather than fabricating a sample rate into a file
that outlives the process — and the Python face could not say it.

`required = true` already meant "reject None" on a capsule param. It had no
contrasting branch, so the key was inert; honouring it is the whole fix, and
the manifest vocabulary is unchanged.

Two things are deliberately **not** here. A nullable handle is still a
required *positional* — accepting `None` and being omittable are different
axes, and a `= None` in the stub that the binding does not honour is the
gh-611 failure this repo already ships a checker for. And the mandatory form
is asserted unchanged throughout, because the interesting risk in a change
like this is not that the new path is wrong but that the old one moved.

The compile-and-run cases reuse gh-790's producer/consumer fixture, whose
patched `capture_create` already reads `tlm ? tlm->magic : 0` — so a NULL
handle is observable as `seen == 0` rather than a crash, which is exactly the
distinction being tested.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._cli_parse import parse_init_param_flag  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from just_makeit._script import _init_param_spec  # noqa: E402

from test_gh790_capsule_init_param import (  # noqa: E402
    CAP,
    FOREIGN_H,
    HDR,
    PTR,
)


def _param(*, nullable: bool) -> tuple:
    """A capsule init-param tuple, mandatory or not. `required` is slot 8."""
    return (
        "tlm",
        PTR,
        "",
        "",
        "",
        "",
        False,
        "",
        not nullable,
        "",
        CAP,
        HDR,
    )


class TestTheGrammar:
    """`--init-param name:type:capsule:<name>[:<header>][:optional]`."""

    @pytest.mark.parametrize(
        "spec,required,header",
        [
            (f"tlm:{PTR}:capsule:{CAP}", True, ""),
            (f"tlm:{PTR}:capsule:{CAP}:{HDR}", True, HDR),
            (f"tlm:{PTR}:capsule:{CAP}:optional", False, ""),
            (f"tlm:{PTR}:capsule:{CAP}:{HDR}:optional", False, HDR),
        ],
    )
    def test_optional_is_matched_as_a_token_not_a_position(
        self, spec, required, header
    ):
        # Matched by name so it reads the same with or without a header. A
        # positional rule would need `…:capsule:cap::optional` for the
        # header-less form, and a header file is never called `optional`.
        p, _ = parse_init_param_flag(["--init-param", spec], 0)
        assert p[8] is required
        assert p[11] == header
        assert p[10] == CAP

    def test_the_default_is_still_mandatory(self):
        p, _ = parse_init_param_flag(
            ["--init-param", f"tlm:{PTR}:capsule:{CAP}"], 0
        )
        assert p[8] is True


class TestItRoundTrips:
    """A nullable handle that replays as mandatory is a silent divergence."""

    def test_the_manifest_records_it_as_the_absence_of_required(
        self, tmp_path
    ):
        cfg = {
            "project": {"name": "x", "version": "0.1.0"},
            "capture": {
                "arg_type": "float",
                "return_type": "float",
                "init_params": [
                    C.init_param_tuple_to_dict(_param(nullable=True))
                ],
            },
        }
        C.save(tmp_path, cfg)
        back = C.load(tmp_path)["capture"]["init_params"][0]
        assert "required" not in back
        assert back["capsule"] == CAP
        # And the projection the renderer reads.
        assert C.init_params(C.load(tmp_path), "capture")[0][8] is False

    def test_jm_script_emits_the_optional_token(self):
        nullable = C.init_param_tuple_to_dict(_param(nullable=True))
        assert (
            _init_param_spec(nullable)
            == f"tlm:{PTR}:capsule:{CAP}:{HDR}:optional"
        )

    def test_jm_script_leaves_a_mandatory_one_alone(self):
        mandatory = C.init_param_tuple_to_dict(_param(nullable=False))
        assert _init_param_spec(mandatory) == f"tlm:{PTR}:capsule:{CAP}:{HDR}"


class TestTheGeneratedSurfaces:
    """What the stub says and what the header promises."""

    def _scaffold(self, tmp_path, *, nullable: bool) -> Path:
        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            d = root / "native" / "inc" / "telemetry"
            d.mkdir(parents=True)
            (d / "telemetry.h").write_text(FOREIGN_H)
            object_run(
                root,
                "capture",
                None,
                state_vars=[("seen", "size_t", "0")],
                init_params=[_param(nullable=nullable)],
            )
        return root

    def test_the_stub_says_none_is_accepted(self, tmp_path):
        root = self._scaffold(tmp_path, nullable=True)
        pyi = (root / "src" / "proj" / "capture.pyi").read_text()
        assert "def __init__(self, tlm: object | None) -> None" in pyi
        # No `= None`: accepting None and being omittable are different
        # axes, and a default the binding does not honour is gh-611.
        assert "tlm: object | None = None" not in pyi

    def test_a_mandatory_handle_keeps_the_bare_annotation(self, tmp_path):
        root = self._scaffold(tmp_path, nullable=False)
        pyi = (root / "src" / "proj" / "capture.pyi").read_text()
        assert "def __init__(self, tlm: object) -> None" in pyi
        # Scoped to the constructor line: `| None` legitimately appears
        # elsewhere in the stub (`__exit__`'s parameters), so a file-wide
        # assertion would pass or fail for the wrong reason.
        assert "tlm: object | None" not in pyi

    def test_the_sacred_header_documents_the_null(self, tmp_path):
        # The author's `create()` has to handle NULL, and the header is where
        # they read its contract.
        root = self._scaffold(tmp_path, nullable=True)
        h = (
            root / "native" / "inc" / "capture" / "capture_core.h"
        ).read_text()
        assert "May be NULL (Python: None)." in h

    def test_a_mandatory_handle_does_not(self, tmp_path):
        root = self._scaffold(tmp_path, nullable=False)
        h = (
            root / "native" / "inc" / "capture" / "capture_core.h"
        ).read_text()
        # The exact sentence, not the substring: `destroy()`'s own
        # `@param state  May be NULL.` is unrelated and always present.
        assert "May be NULL (Python: None)." not in h

    def test_the_binding_guards_on_py_none_rather_than_rejecting_it(
        self, tmp_path
    ):
        root = self._scaffold(tmp_path, nullable=True)
        ext = (
            root / "native" / "src" / "capture" / "capture_ext.c"
        ).read_text()
        assert "if (tlm_obj != Py_None) {" in ext
        assert "is required and cannot be None" not in ext
        # The constructor's friendly type error survives the nullability —
        # `allow_none` used to gate it by accident (see gh-790's tests).
        assert "must be the doppler.telemetry.tlm capsule" in ext

    def test_the_mandatory_binding_is_unchanged(self, tmp_path):
        root = self._scaffold(tmp_path, nullable=False)
        ext = (
            root / "native" / "src" / "capture" / "capture_ext.c"
        ).read_text()
        assert "tlm_obj == Py_None || tlm_obj == NULL" in ext
        assert "is required and cannot be None" in ext


@pytest.mark.skipif(not shutil.which("cmake"), reason="needs a C toolchain")
class TestItCompilesAndRuns:
    """The only assertions that settle whether NULL actually reaches C."""

    def _duo(self, tmp_path: Path) -> Path:
        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            d = root / "native" / "inc" / "telemetry"
            d.mkdir(parents=True)
            (d / "telemetry.h").write_text(FOREIGN_H)
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
                init_params=[_param(nullable=True)],
            )
        core = root / "native" / "src" / "capture" / "capture_core.c"
        s = core.read_text()
        old = f"capture_create({PTR}tlm)\n{{"
        assert old in s, s[:1200]
        # `tlm ? … : 0` is the point: a NULL handle is observable as 0
        # rather than a segfault, so "None reached C as NULL" is a
        # measurement and not an absence of crash.
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

    def test_none_reaches_c_as_null(self, tmp_path):
        out = self._run(
            self._duo(tmp_path),
            "from proj import Capture\nprint(Capture(None).get_seen())\n",
        )
        assert out.strip() == "0"

    def test_a_real_handle_still_crosses(self, tmp_path):
        # The half a nullability change could plausibly break.
        out = self._run(
            self._duo(tmp_path),
            "from proj import Telemetry, Capture\n"
            "t = Telemetry(magic=4242)\n"
            "print(Capture(t).get_seen())\n"
            "print(Capture(t._capsule).get_seen())\n",
        )
        assert out.split() == ["4242", "4242"]

    def test_a_wrong_object_still_names_what_to_pass(self, tmp_path):
        # The regression the `explain_type_error` split exists to prevent:
        # with the old coupling this raised
        # `AttributeError: 'int' object has no attribute '_capsule'`.
        out = self._run(
            self._duo(tmp_path),
            "from proj import Capture\n"
            "try:\n"
            "    Capture(7)\n"
            "except Exception as e:\n"
            "    print(type(e).__name__, e)\n",
        )
        assert out.startswith("TypeError"), out
        assert "must be the doppler.telemetry.tlm capsule" in out


class TestTheModuleFaceAgreesWithItsBinding:
    """gh-845: §H fixed the standalone `.pyi` and missed the module one.

    jm has five `.pyi` producers. §H changed `_context/_state.py`'s (the
    standalone object) and `_stubs.make_module_pyi` kept its own idea of which
    params are required-positional — a predicate that qualified a capsule only
    via the manifest's `required` flag, which was `True` for every capsule
    until §H set it `False` for a nullable one.

    Two divergences followed, and the second is the worse:

    * the stub advertised `clock: Any = ...` for a positional the binding
      demands, so `Capn()` type-checked and raised (the gh-611 shape);
    * the stub left it in declaration order behind a defaulted scalar the
      kwlist hoists it *above*, so `Capn(4096)` bound 4096 to `clock` while
      the stub promised `n` (the gh-823 shape) — a wrong binding, silently.

    Asserted against the generated kwlist rather than against a literal, so
    the two faces cannot drift apart again without this failing.
    """

    def _module_project(self, tmp_path, *, nullable: bool) -> Path:
        root = tmp_path / "proj"
        with contextlib.redirect_stdout(io.StringIO()):
            new_run("proj", root)
            module_run(root, "m")
            d = root / "native" / "inc" / "telemetry"
            d.mkdir(parents=True)
            (d / "telemetry.h").write_text(FOREIGN_H)
            object_run(
                root,
                "capn",
                "m",
                no_state=True,
                no_step=True,
                class_name="Capn",
                init_params=[
                    ("n", "size_t", "1024"),
                    _param(nullable=nullable),
                ],
            )
        return root

    def _faces(self, root: Path) -> tuple[list[str], list[str]]:
        """(stub parameter names, kwlist names) — the pair that must agree."""
        pyi = (root / "src" / "proj" / "m" / "m.pyi").read_text()
        sig = re.search(r"def __init__\(self, (.*?)\) -> None", pyi).group(1)
        stub = [p.split(":")[0].strip() for p in sig.split(", ")]
        ext = (root / "native" / "src" / "m" / "m_ext_capn.c").read_text()
        kw = re.search(r"kwlist\[\] = \{(.*?), NULL\}", ext).group(1)
        return stub, [k.strip().strip('"') for k in kw.split(",")]

    @pytest.mark.parametrize("nullable", [True, False])
    def test_the_stub_and_the_kwlist_agree_on_order(self, tmp_path, nullable):
        stub, kwlist = self._faces(
            self._module_project(tmp_path, nullable=nullable)
        )
        assert stub == kwlist

    def test_a_nullable_handle_is_positional_and_annotated(self, tmp_path):
        root = self._module_project(tmp_path, nullable=True)
        pyi = (root / "src" / "proj" / "m" / "m.pyi").read_text()
        assert "def __init__(self, tlm: object | None, n: int = ...)" in pyi
        # The binding keeps it before the `|`, so a default here would bless
        # a call that raises.
        assert "tlm: object | None = " not in pyi
        assert "tlm: Any" not in pyi

    def test_a_mandatory_handle_is_unchanged(self, tmp_path):
        root = self._module_project(tmp_path, nullable=False)
        pyi = (root / "src" / "proj" / "m" / "m.pyi").read_text()
        assert "def __init__(self, tlm: object, n: int = ...)" in pyi
