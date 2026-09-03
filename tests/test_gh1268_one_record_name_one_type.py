"""gh-1268: one extension module, one type object per public record name.

gh-1264 made a ``single = true`` record's type real — created at module init
and handed to ``PyModule_AddObject`` under its public name. It deduplicated
the list of names to register in :func:`_record.registrations`, which sees
**one component**, while the name being deduplicated is an attribute of the
whole extension **module**. A view and its parent that share a record-returning
method therefore each passed their own "first occurrence" test, and the
aggregator emitted::

    PyModule_AddObject (m, "FrameLayout", (PyObject *)FrameObj_layout_type);
    ...
    PyModule_AddObject (m, "FrameLayout", (PyObject *)FrameDescObj_layout_type);

``PyModule_AddObject`` *steals* the reference it is given. The second call
replaces the module-dict entry and drops the module's only reference to the
first type. The type survives on its own internal self-references (its
``tp_mro`` contains itself; its descriptors point back at it) as an
**unreachable cycle**, so nothing fails immediately — it is freed at the next
GC pass, while ``FrameObj_layout_type`` still points at it, and the next
``PyStructSequence_New`` through that wrapper reads freed memory.

That delay is why this shipped: on a fresh interpreter the call returns a
correct-looking record. Measured in doppler on jm 0.75.3 as ``make test-stubs``
exiting 139; reproduced here as a scaffold that segfaults only once a
``gc.collect()`` has run.

The fix moves the rule to :func:`_record.resolve`, over a namespace the
*module* aggregator keeps for all of its components:

- a repeat of a name already claimed by an identical shape **aliases** the
  first type object (``B_m_type = A_m_type;``) instead of registering a
  second, so both classes return instances of the one class the ``.pyi``
  declares and ``isinstance`` holds for both;
- a repeat with a *different* shape is **refused**: aliasing would fill a
  descriptor of one arity from a kernel of another, and registering both is
  the segfault above. ``jm method`` refuses it before writing anything.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _record  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._record import RecordReg  # noqa: E402
from just_makeit._render import record_registration_c  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")
_LINK = (
    ["-bundle", "-undefined", "dynamic_lookup"]
    if sys.platform == "darwin"
    else ["-shared"]
)

_FIELDS = [
    {"name": "n", "type": "uint64_t", "doc": "How many."},
    {"name": "mean", "type": "double", "doc": "The mean."},
]
_SHAPE = tuple((f["name"], f["type"]) for f in _FIELDS)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _scaffold(tmp_path: Path) -> Path:
    """A module object with a ``single`` record, plus a view sharing it.

    Built by running the real commands rather than by writing a manifest: a
    fixture assembled by hand is a fixture that can stop matching what the
    tool produces, and this bug lives in what the tool produces.
    """
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "m")
    _quiet(
        object_run,
        root,
        "acc",
        "m",
        state_vars=[("sum", "double", "0.0")],
        arg_type="double",
        return_type="double",
        mutable=True,
    )
    _quiet(
        method_run,
        root,
        "acc",
        "summary",
        "m",
        "void",
        "acc_summary_t",
        False,
        [],
        single=True,
        record_name="Summary",
        record_doc="Count and mean.",
        result_fields=[dict(f) for f in _FIELDS],
    )
    _quiet(
        view_run,
        root,
        "acc",
        "SeededAcc",
        "m",
        "acc_create_seeded",
        init_params=[("seed", "double", "0.0")],
    )
    header = root / "native" / "inc" / "acc" / "acc_core.h"
    text = header.read_text(encoding="utf-8")
    text = text.replace(
        "} acc_state_t;",
        "} acc_state_t;\n\n/** @brief Count and mean. */\ntypedef struct {\n"
        "    uint64_t n;  /**< How many. */\n"
        "    double mean; /**< The mean. */\n} acc_summary_t;",
    )
    header.write_text(text, encoding="utf-8")
    return root


def _implement(root: Path) -> None:
    """Fill the two ``<<IMPLEMENT>>`` stubs so the scaffold links."""
    core = root / "native" / "src" / "acc" / "acc_core.c"
    text = core.read_text(encoding="utf-8")
    text = text.replace(
        "    acc_summary_t _r = {0};\n    return _r; /* placeholder */",
        "    acc_summary_t _r = {1, state->sum};\n    return _r;",
    )
    text = re.sub(
        r"/\* <<IMPLEMENT>>: build the state for the SeededAcc view\. \*/\n"
        r"    return NULL;",
        "return acc_create (seed);",
        text,
    )
    core.write_text(text, encoding="utf-8")


class TestResolve:
    """The rule itself: one name, one type."""

    def test_a_first_claim_is_recorded(self):
        seen: dict = {}
        a = RecordReg("Acc_summary", "Summary", _SHAPE)
        assert _record.resolve(a, seen) is None
        assert seen == {"Summary": a}

    def test_a_repeat_of_the_same_shape_aliases_the_first(self):
        seen: dict = {}
        a = RecordReg("Acc_summary", "Summary", _SHAPE)
        b = RecordReg("SeededAcc_summary", "Summary", _SHAPE)
        _record.resolve(a, seen)
        assert _record.resolve(b, seen) is a

    def test_a_repeat_of_a_different_shape_is_refused(self):
        seen: dict = {}
        _record.resolve(RecordReg("Acc_summary", "Summary", _SHAPE), seen)
        with pytest.raises(ValueError) as exc:
            _record.resolve(
                RecordReg("Bcc_summary", "Summary", (("q", "int"),)), seen
            )
        msg = str(exc.value)
        # Actionable: both claimants and both shapes, and the way out.
        assert "Acc_summary" in msg and "Bcc_summary" in msg
        assert "n:uint64_t" in msg and "q:int" in msg
        assert "--record-name" in msg

    def test_different_names_never_collide(self):
        seen: dict = {}
        _record.resolve(RecordReg("A_m", "Summary", _SHAPE), seen)
        assert (
            _record.resolve(RecordReg("B_m", "Other", (("q", "int"),)), seen)
            is None
        )

    def test_name_conflict_reports_the_same_refusal(self):
        assert not _record.name_conflict(
            [
                RecordReg("A_m", "Summary", _SHAPE),
                RecordReg("B_m", "Summary", _SHAPE),
            ]
        )
        assert "--record-name" in _record.name_conflict(
            [
                RecordReg("A_m", "Summary", _SHAPE),
                RecordReg("B_m", "Summary", (("q", "int"),)),
            ]
        )


class TestEmittedC:
    """What `record_registration_c` writes for a shared namespace."""

    def test_a_repeat_aliases_instead_of_registering_twice(self):
        seen: dict = {}
        record_registration_c([RecordReg("A_m", "Summary", _SHAPE)], seen)
        ready, add = record_registration_c(
            [RecordReg("B_m", "Summary", _SHAPE)], seen
        )
        assert add == [], "the second claim must not call PyModule_AddObject"
        assert ready == [
            "    B_m_type = A_m_type;"
            "  /* Summary: one public name, one type */"
        ]

    def test_a_fresh_namespace_registers_normally(self):
        """No `seen` means a standalone object's own `.so` — its own module."""
        _ready, add = record_registration_c(
            [RecordReg("A_m", "Summary", _SHAPE)]
        )
        assert 'PyModule_AddObject(m, "Summary"' in add[0]


class TestTheAggregator:
    """The generated `PyInit_<module>` for a parent and a view."""

    def test_the_name_is_registered_exactly_once(self, tmp_path):
        root = _scaffold(tmp_path)
        agg = (root / "native" / "src" / "m" / "m_ext.c").read_text(
            encoding="utf-8"
        )
        assert agg.count('PyModule_AddObject(m, "Summary"') == 1

    def test_the_views_static_aliases_the_parents(self, tmp_path):
        root = _scaffold(tmp_path)
        agg = (root / "native" / "src" / "m" / "m_ext.c").read_text(
            encoding="utf-8"
        )
        assert "SeededAcc_summary_type = Acc_summary_type;" in agg
        # ...and the alias comes after the type it aliases exists.
        assert agg.index(
            "Acc_summary_type = PyStructSequence_NewType"
        ) < agg.index("SeededAcc_summary_type = Acc_summary_type;")

    def test_the_package_init_exports_the_name_once(self, tmp_path):
        """Enforced by the export merge, not by the list `_object` builds.

        `record_names` there deliberately carries the repeat (a view and its
        parent both name the record); this pins the property at the artifact
        rather than at whichever collapses it.
        """
        root = _scaffold(tmp_path)
        init_py = (root / "src" / "demo" / "m" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert init_py.count('"Summary"') == 1
        assert re.search(r"from \.m import [^\n]*\bSummary\b", init_py)
        assert not re.search(r"Summary,[^\n]*\bSummary\b", init_py)


@_needs_cc
def test_the_built_module_survives_a_collection(tmp_path):
    """The property the text assertions stand in for.

    A `gc.collect()` is what turns the dropped reference into freed memory,
    and the heap churn after it is what makes the read of that memory fail
    rather than quietly succeed. Both are needed: without the collect the
    type is merely unreachable, and without the churn the freed arena still
    holds a plausible-looking type.
    """
    np = pytest.importorskip("numpy")
    root = _scaffold(tmp_path)
    _implement(root)
    pkg = root / "src" / "demo" / "m"
    build = subprocess.run(
        [
            _CC,
            "-fPIC",
            *_LINK,
            "-std=gnu99",
            f"-I{root / 'native' / 'inc'}",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{np.get_include()}",
            str(root / "native" / "src" / "m" / "m_ext.c"),
            str(root / "native" / "src" / "acc" / "acc_core.c"),
            "-o",
            str(pkg / f"m{sysconfig.get_config_var('EXT_SUFFIX')}"),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import gc, sys; sys.path.insert(0, 'src')\n"
            "from demo.m import Acc, SeededAcc, Summary\n"
            "a, s = Acc(1.0), SeededAcc(2.0)\n"
            "gc.collect()\n"
            "junk = [bytearray(400) for _ in range(20000)]\n"
            "r1, r2 = a.summary(), s.summary()\n"
            "print('parent', isinstance(r1, Summary))\n"
            "print('view', isinstance(r2, Summary))\n"
            "print('one', type(r1) is type(r2))\n",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stderr}"
    assert "parent True" in r.stdout, r.stdout
    assert "view True" in r.stdout, r.stdout
    assert "one True" in r.stdout, r.stdout


class TestTheCliRefusesBeforeWriting:
    """A conflicting `--record-name` leaves no half-made tree."""

    def _two_objects(self, tmp_path: Path) -> Path:
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(module_run, root, "m")
        for obj in ("a", "b"):
            _quiet(
                object_run,
                root,
                obj,
                "m",
                state_vars=[("x", "double", "0.0")],
                arg_type="double",
                return_type="double",
            )
        _quiet(
            method_run,
            root,
            "a",
            "sum",
            "m",
            "void",
            "a_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            result_fields=[dict(f) for f in _FIELDS],
        )
        return root

    def test_a_conflicting_shape_exits_1(self, tmp_path, capsys):
        root = self._two_objects(tmp_path)
        with pytest.raises(SystemExit) as exc:
            _quiet(
                method_run,
                root,
                "b",
                "sum",
                "m",
                "void",
                "b_sum_t",
                False,
                [],
                single=True,
                record_name="Sum",
                result_fields=[{"name": "q", "type": "int"}],
            )
        assert exc.value.code == 1
        assert "--record-name" in capsys.readouterr().err

    def test_nothing_was_written(self, tmp_path):
        root = self._two_objects(tmp_path)
        before = {
            p: p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and "build" not in p.parts
        }
        with pytest.raises(SystemExit):
            _quiet(
                method_run,
                root,
                "b",
                "sum",
                "m",
                "void",
                "b_sum_t",
                False,
                [],
                single=True,
                record_name="Sum",
                result_fields=[{"name": "q", "type": "int"}],
            )
        after = {
            p: p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and "build" not in p.parts
        }
        assert after == before

    def test_the_same_shape_is_allowed(self, tmp_path):
        """Two objects may legitimately return the same record."""
        root = self._two_objects(tmp_path)
        _quiet(
            method_run,
            root,
            "b",
            "sum",
            "m",
            "void",
            "a_sum_t",
            False,
            [],
            single=True,
            record_name="Sum",
            result_fields=[dict(f) for f in _FIELDS],
        )
        agg = (root / "native" / "src" / "m" / "m_ext.c").read_text(
            encoding="utf-8"
        )
        assert agg.count('PyModule_AddObject(m, "Sum"') == 1
        assert "B_sum_type = A_sum_type;" in agg
