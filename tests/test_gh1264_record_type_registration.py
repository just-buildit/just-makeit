"""gh-1264: a ``single = true`` record's runtime type was never registered.

A `single = true` method with `record_name = "X"` renders two faces that used
to disagree. The `.pyi` declares `class X(tuple[...])` at module level
(`_record.pyi_classes`), so a reader and a type checker both expect
`from pkg.mod import X`. The C binding built the `PyStructSequence` type
*lazily*, inside the method wrapper (`if (!Obj_m_type) Obj_m_type =
PyStructSequence_NewType(&desc);`), and never handed it to
`PyModule_AddObject` -- so at runtime `pkg.mod.X` did not exist, even after
the method had run and returned an instance. `type(r).__name__` read `"X"`
while `hasattr(pkg.mod, "X")` stayed `False`: the only test a consumer could
write was a string compare on the type's name, since `isinstance(r, X)` and
a docs directive naming `pkg.mod.X` both had nothing to bind to.

Measured in doppler on jm 0.73.1 for every record it has: `doppler.ber
.BerInterval`, `doppler.measure.ToneMetrics`, `doppler.dsss.ReceiverStatus`.

Fixed by creating the type EAGERLY at module init -- the same two-phase
`PyType_Ready`/`PyModule_AddObject` split every other jm-owned type already
goes through -- and registering it under its public name, in three places
that used to know nothing about records at all:

- the standalone object's own `PyInit_` (a new pair of template slots,
  `record_type_ready`/`record_add_object`, seeded empty so an object with no
  record renders byte-identical to before);
- the module aggregator's `PyInit_<module>` (built the same way every other
  type-ready/add-object pair already was, from `record_registration_c`);
- the module subpackage's `__init__.py` re-export list, which advertised
  only the Component classes (`all_exports` in `_object.py`) -- without this
  the type became reachable as `pkg.mod.mod.X` (the raw extension) but not
  `pkg.mod.X`, the path both the `.pyi` and `record_module` advertise.

The lazy in-method creation is left in place as a harmless fallback (it is
idempotent -- one pointer check -- and self-heals a fragment that predates
this feature and has not been regenerated); this fix's whole job is making
sure something ALSO registers the type where jm's other faces say it lives.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._record import RecordReg, registrations  # noqa: E402
from just_makeit._render import record_registration_c  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

_FIELDS = [
    {"name": "found", "type": "int", "doc": "Nonzero on a hit."},
    {"name": "offset", "type": "size_t", "doc": "Bit offset."},
]


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _declare_hit_struct(header: Path) -> None:
    """Insert the ``sync_hit_t`` the manifest's ``return_type`` names.

    jm never sees the struct itself -- it is the author's, in the sacred
    header -- so every scaffold needs this by hand, the same as a real
    project would write it before implementing the method.
    """
    text = header.read_text(encoding="utf-8")
    text = text.replace(
        "} sync_state_t;",
        "} sync_state_t;\n\ntypedef struct {\n    int found;\n"
        "    size_t offset;\n} sync_hit_t;",
    )
    header.write_text(text, encoding="utf-8")


def _scaffold_standalone(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    _quiet(
        new_run,
        "demo",
        root,
    )
    _quiet(
        object_run,
        root,
        "sync",
        None,
        state_vars=[("marker", "uint64_t", "0")],
        arg_type="void",
        return_type="float",
    )
    _quiet(
        method_run,
        root,
        "sync",
        "find",
        None,
        "float[]",
        "sync_hit_t",
        False,
        [],
        params=[("max_errors", "int")],
        single=True,
        record_name="SyncHit",
        result_fields=[dict(f) for f in _FIELDS],
    )
    _declare_hit_struct(root / "native" / "inc" / "sync" / "sync_core.h")
    return root


def _scaffold_module(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "m")
    _quiet(
        object_run,
        root,
        "sync",
        "m",
        state_vars=[("marker", "uint64_t", "0")],
        arg_type="void",
        return_type="float",
    )
    _quiet(
        method_run,
        root,
        "sync",
        "find",
        "m",
        "float[]",
        "sync_hit_t",
        False,
        [],
        params=[("max_errors", "int")],
        single=True,
        record_name="SyncHit",
        result_fields=[dict(f) for f in _FIELDS],
    )
    _declare_hit_struct(root / "native" / "inc" / "sync" / "sync_core.h")
    return root


_SHAPE = tuple((f["name"], f["type"]) for f in _FIELDS)


class TestRegistrationsHelper:
    """Unit-level: the RecordReg list itself."""

    def test_a_record_method_is_found(self):
        methods = [
            {
                "name": "find",
                "single": True,
                "record_name": "SyncHit",
                "result_fields": _FIELDS,
            }
        ]
        assert registrations(methods, "Sync") == [
            RecordReg("Sync_find", "SyncHit", _SHAPE, "SyncHit(found, offset)")
        ]

    def test_a_non_record_method_is_ignored(self):
        methods = [{"name": "step", "arg_type": "float"}]
        assert registrations(methods, "Sync") == []

    def test_no_methods_is_empty(self):
        assert registrations([], "Sync") == []

    def test_two_methods_sharing_a_record_name_are_both_listed(self):
        """gh-1268 moved the deduplication OUT of here.

        This function sees one component; the name it was deduplicating is
        an attribute of the whole extension MODULE. Dropping the repeat here
        meant each component passed its own "first occurrence" test, the
        aggregator registered two type objects under one key, and the second
        `PyModule_AddObject` freed the first. `_record.resolve` owns the
        rule now, over a namespace the caller keeps for the whole module.
        """
        methods = [
            {
                "name": "find",
                "single": True,
                "record_name": "Hit",
                "result_fields": _FIELDS,
            },
            {
                "name": "find2",
                "single": True,
                "record_name": "Hit",
                "result_fields": _FIELDS,
            },
        ]
        assert registrations(methods, "Sync") == [
            RecordReg("Sync_find", "Hit", _SHAPE, "Hit(found, offset)"),
            RecordReg("Sync_find2", "Hit", _SHAPE, "Hit(found, offset)"),
        ]


class TestRecordRegistrationC:
    """Unit-level: the C emitted for a RecordReg list."""

    def test_empty_list_emits_nothing(self):
        assert record_registration_c([]) == ([], [])

    def test_one_entry_creates_then_registers(self):
        ready, add = record_registration_c(
            [RecordReg("Sync_find", "SyncHit", _SHAPE)]
        )
        assert len(ready) == 1
        assert "PyStructSequence_NewType(&Sync_find_desc)" in ready[0]
        assert len(add) == 1
        assert 'PyModule_AddObject(m, "SyncHit"' in add[0]
        assert "(PyObject *)Sync_find_type" in add[0]
        # gh-1264's second bug (fixed before it shipped): a HEAP type from
        # PyStructSequence_NewType is already an owned reference -- no
        # Py_INCREF before handing it to AddObject, unlike a static
        # PyTypeObject. Only the failure path decrefs.
        assert "Py_INCREF" not in add[0]
        assert "Py_DECREF(Sync_find_type)" in add[0]


class TestStandaloneObject:
    """A standalone object's own `_ext.c` / `PyInit_`."""

    def test_the_type_is_created_and_registered_at_init(self, tmp_path):
        root = _scaffold_standalone(tmp_path)
        ext = (root / "native" / "src" / "sync" / "sync_ext.c").read_text(
            encoding="utf-8"
        )
        m = re.search(
            r"PyMODINIT_FUNC\nPyInit_sync\(void\)\n\{(.*?)\n\}", ext, re.S
        )
        assert m, "no PyInit_sync found"
        body = m.group(1)
        # created BEFORE the module object exists...
        create_at = body.index("PyStructSequence_NewType(&Sync_find_desc)")
        module_at = body.index("PyModule_Create(")
        assert create_at < module_at
        # ...and registered under its public name AFTER.
        add_at = body.index('PyModule_AddObject(m, "SyncHit"')
        assert add_at > module_at

    def test_an_object_with_no_record_renders_unchanged(self, tmp_path):
        """Zero-churn: the new template slots must vanish for every project
        this feature does not apply to, not just print an empty comment."""
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "osc",
            None,
            state_vars=[("gain", "float", "0.0f")],
            arg_type="float",
            return_type="float",
        )
        ext = (root / "native" / "src" / "osc" / "osc_ext.c").read_text(
            encoding="utf-8"
        )
        assert "PyStructSequence" not in ext
        assert "record_type_ready" not in ext
        assert "record_add_object" not in ext

    @_needs_cc
    def test_the_generated_ext_c_compiles(self, tmp_path):
        numpy = pytest.importorskip("numpy")
        root = _scaffold_standalone(tmp_path)
        proc = subprocess.run(
            [
                _CC,
                "-fsyntax-only",
                "-std=gnu99",
                f"-I{root / 'native' / 'inc'}",
                f"-I{sysconfig.get_paths()['include']}",
                f"-I{numpy.get_include()}",
                str(root / "native" / "src" / "sync" / "sync_ext.c"),
            ],
            capture_output=True,
            text=True,
            cwd=os.fspath(root),
        )
        assert proc.returncode == 0, proc.stderr


class TestModuleAggregatedObject:
    """A module object's aggregator `PyInit_<module>` and its `__init__.py`."""

    def test_the_type_is_created_and_registered_at_init(self, tmp_path):
        root = _scaffold_module(tmp_path)
        agg = (root / "native" / "src" / "m" / "m_ext.c").read_text(
            encoding="utf-8"
        )
        assert (
            "Sync_find_type = PyStructSequence_NewType(&Sync_find_desc)" in agg
        )
        assert (
            'PyModule_AddObject(m, "SyncHit", (PyObject *)Sync_find_type)'
            in agg
        )

    def test_the_package_init_reexports_it(self, tmp_path):
        root = _scaffold_module(tmp_path)
        init_py = (root / "src" / "demo" / "m" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "SyncHit" in init_py
        assert re.search(r"from \.m import Sync, SyncHit", init_py)
        assert '"SyncHit"' in init_py

    @_needs_cc
    def test_the_generated_aggregator_compiles(self, tmp_path):
        numpy = pytest.importorskip("numpy")
        root = _scaffold_module(tmp_path)
        proc = subprocess.run(
            [
                _CC,
                "-fsyntax-only",
                "-std=gnu99",
                f"-I{root / 'native' / 'inc'}",
                f"-I{sysconfig.get_paths()['include']}",
                f"-I{numpy.get_include()}",
                str(root / "native" / "src" / "m" / "m_ext.c"),
            ],
            capture_output=True,
            text=True,
            cwd=os.fspath(root),
        )
        assert proc.returncode == 0, proc.stderr


class TestRenderModuleExtCPeer:
    """``_render.render_module_ext_c`` duplicates `render_module_ext_aggregator`
    (the docstring at gh-1181 says the extraction happened so the writer and
    the drift oracle "call ONE thing" -- but this second copy still exists,
    called with real `comp_ctxs` by nothing in the production tree today).
    Two implementations of the same loop drift silently unless something
    exercises both, so this proves the fix directly rather than leaving it
    unproven on one of the two copies that carries it.
    """

    def test_it_also_creates_and_registers_the_record_type(self, tmp_path):
        from just_makeit import _config as C
        from just_makeit._object import build_component_ctxs
        from just_makeit._render import render_module_ext_c

        root = _scaffold_module(tmp_path)
        cfg = C.load(root)
        comp_ctxs = build_component_ctxs(root, cfg, "m", "demo")
        out = render_module_ext_c("m", comp_ctxs)
        assert (
            "Sync_find_type = PyStructSequence_NewType(&Sync_find_desc)" in out
        )
        assert (
            'PyModule_AddObject(m, "SyncHit", (PyObject *)Sync_find_type)'
            in out
        )


class TestApplyIsIdempotent:
    """A second `apply` on an unchanged manifest must not report drift --
    the exact property that keeps `jm status --check` quiet."""

    def test_a_second_apply_changes_nothing(self, tmp_path):
        from just_makeit._apply import run as apply_run

        root = _scaffold_standalone(tmp_path)
        ext_path = root / "native" / "src" / "sync" / "sync_ext.c"
        before = ext_path.read_text(encoding="utf-8")
        _quiet(apply_run, root)
        assert ext_path.read_text(encoding="utf-8") == before
