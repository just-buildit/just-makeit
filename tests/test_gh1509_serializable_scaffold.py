"""gh-1509: a fresh ``--serializable`` object compiles; its state round-trips.

gh-400 generates the Python ``state_bytes``/``get_state``/``set_state`` over a
C triplet -- ``<c>_state_bytes``, ``<c>_get_state``, ``<c>_set_state`` -- and
left all three to the author: declared in no header, defined nowhere. The
binding jm had just written therefore called three undeclared functions, and
on any compiler that treats an implicit declaration as an error (GCC 14+,
clang) the scaffold did not build.

The triplet is now scaffolded like every other function the binding calls:

- **declared** in the sacred ``_core.h``, and injected by ``apply`` into an
  existing header that lacks it (a module object's header is refreshed
  additively, so its want-list names the triplet too);
- **defined** in ``_core.c`` -- a working implementation that packs the
  declared fields when every field's bytes are its value, a refusing stub
  (``set_state`` -> ``ValueError``) when one is not;
- inline in the header for a header-only core.

What is deliberately NOT done: splicing the bodies into an EXISTING
``_core.c``. Before gh-1509 every serializable object that built had written
its own triplet, and doppler writes it through a macro in 18 cores
(``DP_DEFINE_POD_STATE(boxcar, ...)``) that no source reader sees as a
definition -- splicing appended a duplicate to each. The class this pins:
jm's existing gh-1294 splice meeting a function the author supplied first.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import re
import shutil
import sys
from pathlib import Path

import pytest

from _jmrun import JmRun, run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._context import state_blob_fields  # noqa: E402

_TRIPLET = ("state_bytes", "get_state", "set_state")

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)


def _cli(*args, cwd) -> JmRun:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _h(root: Path, comp: str) -> str:
    return (root / INC_ROOT / comp / f"{comp}_core.h").read_text(
        encoding="utf-8"
    )


def _c(root: Path, comp: str) -> Path:
    return root / "native" / "src" / comp / f"{comp}_core.c"


def _prototypes(text: str, comp: str) -> list[str]:
    """Every top-level ``<comp>_<triplet>(...);`` prototype, one per match."""
    names = "|".join(_TRIPLET)
    return re.findall(
        rf"^[A-Za-z_][^;{{}}\n]*\b({comp}_(?:{names}))\([^;{{}}]*\);$",
        text,
        re.MULTILINE,
    )


def _definitions(text: str, comp: str) -> list[str]:
    """Every ``<comp>_<triplet>(...)`` followed by a body brace."""
    names = "|".join(_TRIPLET)
    return re.findall(
        rf"^(?:static inline )?[A-Za-z_][\w ]*\n?({comp}_(?:{names}))"
        rf"\([^;{{}}]*\)\n\{{",
        text,
        re.MULTILINE,
    )


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """One tree, every shape the triplet has to take."""
    base = tmp_path_factory.mktemp("gh1509")
    _cli("new", "demo", "--no-c-prefix", "--object", "gain", cwd=base)
    root = base / "demo"
    # Plain fields, including a fixed array: the working implementation.
    _cli(
        "object", "osc", "--serializable",
        "--state", "phase:double:0.0", "--state", "taps:float[4]:0",
        cwd=root,
    )  # fmt: skip
    # A module object: its header takes the additive path.
    _cli("module", "m", cwd=root)
    _cli(
        "object", "mo", "--module", "m", "--serializable",
        "--state", "k:int:3",
        cwd=root,
    )  # fmt: skip
    # A field that owns memory elsewhere: the refusing stub.
    _cli(
        "object", "arr", "--serializable", "--array-arg", "coeffs:float32",
        cwd=root,
    )  # fmt: skip
    # Header-only: the definitions go inline.
    _cli(
        "object", "ho", "--serializable", "--header-only",
        "--state", "x:float:1.5",
        cwd=root,
    )  # fmt: skip
    return root


class TestStateBlobFields:
    """Which fields a scaffolded blob can copy byte for byte."""

    def test_scalars_and_fixed_arrays_qualify(self):
        assert state_blob_fields(
            [
                ("p", "double", "0"),
                ("t", "float[4]", ""),
                ("z", "float _Complex", "0"),
            ]
        ) == ["p", "t", "z"]

    @pytest.mark.parametrize(
        "vars_, opaque, arrays",
        [
            ([("s", "const char *", "NULL")], (), ()),
            ([("g", "float", "0")], [("h", "void *")], ()),
            ([("g", "float", "0")], (), [("coeffs", "float32")]),
            ([("f", "my_foreign_t", "0")], (), ()),
        ],
        ids=["string", "opaque-field", "array-arg", "unknown-type"],
    )
    def test_a_field_that_owns_memory_elsewhere_refuses_the_lot(
        self, vars_, opaque, arrays
    ):
        assert state_blob_fields(vars_, opaque, arrays) is None


class TestScaffold:
    """What a fresh tree carries, for each shape -- read, not built."""

    @pytest.mark.parametrize("comp", ["osc", "mo", "arr"])
    def test_the_header_declares_each_exactly_once(self, project, comp):
        assert sorted(_prototypes(_h(project, comp), comp)) == sorted(
            f"{comp}_{n}" for n in _TRIPLET
        )

    @pytest.mark.parametrize("comp", ["osc", "mo", "arr"])
    def test_core_c_defines_each_exactly_once(self, project, comp):
        text = _c(project, comp).read_text(encoding="utf-8")
        assert sorted(_definitions(text, comp)) == sorted(
            f"{comp}_{n}" for n in _TRIPLET
        )

    def test_the_blob_is_the_declared_fields(self, project):
        text = _c(project, "osc").read_text(encoding="utf-8")
        blob = text[text.index("/* The state blob") :]
        assert "return sizeof(state->phase) + sizeof(state->taps);" in blob
        assert blob.count("memcpy(") == 4

    def test_a_field_jm_cannot_copy_gets_a_stub_that_refuses(self, project):
        text = _c(project, "arr").read_text(encoding="utf-8")
        body = text[text.index("arr_set_state(") :]
        assert "return -1;" in body.split("}")[0]
        assert "IMPLEMENT" in text

    def test_header_only_defines_inline_and_declares_nothing_extern(
        self, project
    ):
        h = _h(project, "ho")
        # gh-1679: each is declared `static inline` above the inline step,
        # which may call it; an EXTERN prototype is the compile error.
        protos = _prototypes(h, "ho")
        assert sorted(protos) == sorted(f"ho_{n}" for n in _TRIPLET), h
        assert all(
            ln.startswith("static inline ")
            for ln in h.splitlines()
            if ln.endswith(");") and any(f"ho_{n}(" in ln for n in _TRIPLET)
        ), h
        assert sorted(_definitions(h, "ho")) == sorted(
            f"ho_{n}" for n in _TRIPLET
        )
        assert h.count("static inline size_t\nho_state_bytes(") == 1
        assert not _c(project, "ho").exists()

    def test_an_object_that_is_not_serializable_gains_nothing(self, project):
        for text in (
            _h(project, "gain"),
            _c(project, "gain").read_text(encoding="utf-8"),
        ):
            assert "state_bytes" not in text
            assert "serializable" not in text


_MACRO_DEFINED = """
#include <string.h>
/* The triplet, the way doppler writes it: through a macro no source
 * reader sees as a definition. */
#define DEFINE_POD_STATE(c)                                              \\
    size_t c##_state_bytes(const c##_state_t *s) { return sizeof(*s); } \\
    void c##_get_state(const c##_state_t *s, void *b)                   \\
    { memcpy(b, s, sizeof(*s)); }                                        \\
    int c##_set_state(c##_state_t *s, const void *b)                    \\
    { memcpy(s, b, sizeof(*s)); return 0; }
DEFINE_POD_STATE(osc)
"""


class TestExistingProject:
    """``apply`` on a tree scaffolded before gh-1509."""

    @pytest.fixture
    def old(self, tmp_path) -> Path:
        _cli("new", "old", "--no-c-prefix", cwd=tmp_path)
        root = tmp_path / "old"
        _cli("module", "m", cwd=root)
        for args in (
            ("osc", "--serializable", "--state", "phase:double:0.0"),
            ("mo", "--module", "m", "--serializable", "--state", "k:int:3"),
        ):
            _cli("object", *args, cwd=root)
        # Rewind both to the pre-gh-1509 shape: no prototypes, and the
        # bodies supplied by the author in a form jm cannot read.
        for comp in ("osc", "mo"):
            h = root / INC_ROOT / comp / f"{comp}_core.h"
            h.write_text(
                re.sub(
                    rf"\n\n/\*\* @brief [^\n]*\*/\n[^\n]*{comp}_"
                    rf"(?:{'|'.join(_TRIPLET)})\([^\n]*;",
                    "",
                    h.read_text(encoding="utf-8"),
                ),
                encoding="utf-8",
            )
            assert _prototypes(h.read_text(encoding="utf-8"), comp) == []
            c = _c(root, comp)
            text = c.read_text(encoding="utf-8")
            cut = text.index("/* The state blob")
            c.write_text(
                text[:cut] + _MACRO_DEFINED.replace("(osc)", f"({comp})"),
                encoding="utf-8",
            )
        return root

    @pytest.mark.parametrize("comp", ["osc", "mo"])
    def test_apply_declares_the_triplet(self, old, comp):
        _cli("apply", cwd=old)
        assert sorted(_prototypes(_h(old, comp), comp)) == sorted(
            f"{comp}_{n}" for n in _TRIPLET
        )

    @pytest.mark.parametrize("comp", ["osc", "mo"])
    def test_apply_never_appends_a_second_definition(self, old, comp):
        before = _c(old, comp).read_text(encoding="utf-8")
        _cli("apply", cwd=old)
        assert _c(old, comp).read_text(encoding="utf-8") == before


_ROUNDTRIP = """
import pytest

from demo import Arr, Ho, Osc
from demo.m import Mo


@pytest.mark.parametrize(
    "make",
    [
        lambda: Osc(phase=1.25),
        lambda: Mo(k=7),
        lambda: Ho(x=2.5),
    ],
    ids=["standalone", "module", "header-only"],
)
def test_state_round_trips(make):
    a = make()
    blob = a.get_state()
    assert len(blob) == a.state_bytes() > 0
    b = type(a)()
    assert b.get_state() != blob
    b.set_state(blob)
    assert b.get_state() == blob


def test_the_blob_carries_every_declared_field():
    import numpy as np

    a = Osc(phase=1.25)
    a.set_taps(np.arange(4, dtype=np.float32))
    b = Osc()
    b.set_state(a.get_state())
    assert b.get_phase() == 1.25
    assert list(b.get_taps()) == [0.0, 1.0, 2.0, 3.0]


def test_a_stub_that_cannot_serialize_says_so():
    import numpy as np

    a = Arr(np.ones(3, dtype=np.float32))
    assert a.state_bytes() == 0
    with pytest.raises(ValueError, match="rejected"):
        a.set_state(a.get_state())
"""


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
def test_the_scaffold_builds_and_the_state_round_trips(project):
    tests = project / "src" / "demo" / "tests" / "test_gh1509_roundtrip.py"
    tests.write_text(_ROUNDTRIP, encoding="utf-8")
    r = run_cli("test", cwd=project)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-4000:]
    assert "test_gh1509_roundtrip" in r.stdout + r.stderr
