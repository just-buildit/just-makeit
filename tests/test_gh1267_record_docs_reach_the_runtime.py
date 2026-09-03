"""gh-1267: a record's docs reach the runtime type, not only the ``.pyi``.

gh-646 made ``_record.descriptor_c`` emit the record type's own doc and each
field's doc into the two ``PyStructSequence`` tables, from the same
``_record.fields`` / ``_record.type_doc`` calls the ``.pyi`` writer uses. The
emitter has been correct ever since -- what was missing is that **nothing
refreshes an existing fragment**.

A module object's ``native/src/<mod>/<mod>_ext_<obj>.c`` is *sacred*: it is
spliced, never re-rendered, so hand-written bindings survive. `_docsync`
exists to carry derived prose into it anyway, but it knew only about
``PyMethodDef``, ``PyGetSetDef`` and ``tp_doc``. The structseq descriptor was
therefore frozen at the moment the method was first declared. Documenting the
record afterwards -- ``/**< … */`` on the struct members in the sacred header,
or ``record_doc`` / a ``--result-field`` doc in the manifest -- reached the
``.pyi`` on the next ``apply`` and stopped:

    .pyi:  '''Count and mean.'''  + a full Attributes table
    C:     {"n", NULL}, {"mean", NULL} / "Sum(n, mean)"

which is exactly the two-faces-disagree bug gh-646 exists to prevent, arriving
through the one path that skips the renderer. The standalone path never had
it: `regenerate_standalone` re-renders ``<comp>_ext.c`` wholesale.

Measured in doppler on 0.75.3, where gh-1264 made these types real module
attributes and so made the gap visible: ``ToneMetrics.__doc__`` was ``''`` and
``ToneMetrics.snr.__doc__`` was ``None`` while the stub carried both.
``ReceiverStatus`` had its field docs -- its fragment happened to be generated
when the header already carried them.

Fixed by teaching the transplant the two tables. These slots have no
hand-written variant to protect: the whole descriptor is generated from the
manifest and the header, and the author's channel for the prose is those, so a
slot disagreeing with the reference is stale by construction.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docsync import (  # noqa: E402
    _code_mask,
    _record_doc_slots,
    transplant_docs,
)
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")
_LINK = (
    ["-bundle", "-undefined", "dynamic_lookup"]
    if sys.platform == "darwin"
    else ["-shared"]
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


_UNDOCUMENTED = """\
static PyStructSequence_Field O_m1_fields[] = {
    {"n", NULL},
    {"mean", NULL},
    {NULL, NULL},
};
static PyStructSequence_Desc O_m1_desc = {
    "o.Sum", "Sum(n, mean)", O_m1_fields, 2
};
static PyTypeObject *O_m1_type = NULL;
"""

_DOCUMENTED = """\
static PyStructSequence_Field O_m1_fields[] = {
    {"n", "How many."},
    {"mean", "The mean."},
    {NULL, NULL},
};
static PyStructSequence_Desc O_m1_desc = {
    "o.Sum", "Count and mean.", O_m1_fields, 2
};
static PyTypeObject *O_m1_type = NULL;
"""


class TestSlots:
    """`_record_doc_slots` finds both tables, keyed by sid."""

    def test_it_finds_every_field_doc_and_the_type_doc(self):
        slots = _record_doc_slots(_DOCUMENTED, _code_mask(_DOCUMENTED))
        assert set(slots) == {("O_m1", "n"), ("O_m1", "mean"), ("O_m1", "")}
        assert slots[("O_m1", "n")][2].strip() == '"How many."'
        assert slots[("O_m1", "")][2].strip() == '"Count and mean."'

    def test_the_null_sentinel_row_is_not_a_field(self):
        slots = _record_doc_slots(_UNDOCUMENTED, _code_mask(_UNDOCUMENTED))
        assert ("O_m1", None) not in slots
        assert len(slots) == 3

    def test_a_file_with_no_record_has_no_slots(self):
        src = 'static PyMethodDef X_methods[] = {{"step", f, 0, "d"}};'
        assert _record_doc_slots(src, _code_mask(src)) == {}

    def test_two_records_in_one_file_stay_separate(self):
        both = _DOCUMENTED + _UNDOCUMENTED.replace("O_m1", "O_m2")
        slots = _record_doc_slots(both, _code_mask(both))
        assert slots[("O_m1", "n")][2].strip() == '"How many."'
        assert slots[("O_m2", "n")][2].strip() == "NULL"


class TestTransplant:
    """A stale descriptor is refreshed from the reference render."""

    def test_null_docs_are_replaced(self):
        out = transplant_docs(_UNDOCUMENTED, _DOCUMENTED, _UNDOCUMENTED)
        assert '{"n", "How many."}' in out
        assert '{"mean", "The mean."}' in out
        assert '"Count and mean."' in out
        assert "NULL, NULL" in out, "the sentinel row must survive"

    def test_the_synopsis_fallback_yields_to_a_declared_doc(self):
        """`Sum(n, mean)` is jm's own derived stand-in, not authored prose."""
        out = transplant_docs(_UNDOCUMENTED, _DOCUMENTED, _UNDOCUMENTED)
        assert "Sum(n, mean)" not in out

    def test_it_is_idempotent(self):
        assert (
            transplant_docs(_DOCUMENTED, _DOCUMENTED, _DOCUMENTED)
            == _DOCUMENTED
        )

    def test_a_record_absent_from_the_reference_is_untouched(self):
        """A hand-written record the manifest does not describe stays put."""
        out = transplant_docs(_UNDOCUMENTED, "/* nothing */", _UNDOCUMENTED)
        assert out == _UNDOCUMENTED


def _scaffold(tmp_path: Path) -> Path:
    """A module record declared BEFORE anything documents it.

    The order is the whole point: a fragment generated while the struct and
    the manifest are still bare is what freezes the `NULL`s in place.
    """
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(module_run, root, "mm")
    _quiet(
        object_run,
        root,
        "o",
        "mm",
        state_vars=[("x", "double", "0.0")],
        arg_type="double",
        return_type="double",
    )
    header = root / "native" / "inc" / "o" / "o_core.h"
    header.write_text(
        header.read_text(encoding="utf-8").replace(
            "} o_state_t;",
            "} o_state_t;\n\ntypedef struct {\n    uint64_t n;\n"
            "    double mean;\n} o_sum_t;",
        ),
        encoding="utf-8",
    )
    _quiet(
        method_run,
        root,
        "o",
        "m1",
        "mm",
        "void",
        "o_sum_t",
        False,
        [],
        single=True,
        record_name="Sum",
        result_fields=[
            {"name": "n", "type": "uint64_t"},
            {"name": "mean", "type": "double"},
        ],
    )
    return root


def _document(root: Path) -> None:
    """Document the record afterwards, the two ways an author can."""
    header = root / "native" / "inc" / "o" / "o_core.h"
    header.write_text(
        header.read_text(encoding="utf-8").replace(
            "    uint64_t n;\n    double mean;",
            "    uint64_t n;  /**< How many. */\n"
            "    double mean; /**< The mean. */",
        ),
        encoding="utf-8",
    )
    for cand in list((root / "objects").glob("*.toml")) + [
        root / "just-makeit.toml"
    ]:
        text = cand.read_text(encoding="utf-8")
        if 'record_name = "Sum"' in text:
            cand.write_text(
                text.replace(
                    'record_name = "Sum"',
                    'record_name = "Sum"\nrecord_doc = "Count and mean."',
                ),
                encoding="utf-8",
            )
            return
    raise AssertionError("record_name not found in any manifest file")


class TestApplyRefreshesTheFragment:
    def test_the_descriptor_is_frozen_until_apply_runs(self, tmp_path):
        """The starting state, stated so the fix has something to move."""
        root = _scaffold(tmp_path)
        frag = (root / "native" / "src" / "mm" / "mm_ext_o.c").read_text(
            encoding="utf-8"
        )
        assert '{"n", NULL}' in frag

    def test_apply_carries_both_doc_sources_into_the_fragment(self, tmp_path):
        from just_makeit._apply import run as apply_run

        root = _scaffold(tmp_path)
        _document(root)
        _quiet(apply_run, root)
        frag = (root / "native" / "src" / "mm" / "mm_ext_o.c").read_text(
            encoding="utf-8"
        )
        # the header's `/**<` member docs...
        assert '{"n", "How many."}' in frag
        assert '{"mean", "The mean."}' in frag
        # ...and the manifest's record_doc.
        assert '"Count and mean."' in frag

    def test_the_pyi_and_the_fragment_agree(self, tmp_path):
        """The property, rather than either face on its own."""
        from just_makeit._apply import run as apply_run

        root = _scaffold(tmp_path)
        _document(root)
        _quiet(apply_run, root)
        frag = (root / "native" / "src" / "mm" / "mm_ext_o.c").read_text(
            encoding="utf-8"
        )
        pyi = (root / "src" / "demo" / "mm" / "mm.pyi").read_text(
            encoding="utf-8"
        )
        for prose in ("Count and mean.", "How many.", "The mean."):
            assert prose in pyi, f"{prose!r} missing from the .pyi"
            assert prose in frag, f"{prose!r} missing from the fragment"

    def test_a_second_apply_changes_nothing(self, tmp_path):
        from just_makeit._apply import run as apply_run

        root = _scaffold(tmp_path)
        _document(root)
        _quiet(apply_run, root)
        path = root / "native" / "src" / "mm" / "mm_ext_o.c"
        before = path.read_text(encoding="utf-8")
        _quiet(apply_run, root)
        assert path.read_text(encoding="utf-8") == before


@_needs_cc
def test_the_built_type_carries_the_docs(tmp_path):
    """`help()` on the built module, which is what the issue measured."""
    from just_makeit._apply import run as apply_run

    np = pytest.importorskip("numpy")
    root = _scaffold(tmp_path)
    _document(root)
    _quiet(apply_run, root)
    core = root / "native" / "src" / "o" / "o_core.c"
    core.write_text(
        core.read_text(encoding="utf-8").replace(
            "    o_sum_t _r = {0};\n    return _r; /* placeholder */",
            "    o_sum_t _r = {1, state->x};\n    return _r;",
        ),
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            _CC,
            "-fPIC",
            *_LINK,
            "-std=gnu99",
            f"-I{root / 'native' / 'inc'}",
            f"-I{sysconfig.get_paths()['include']}",
            f"-I{np.get_include()}",
            str(root / "native" / "src" / "mm" / "mm_ext.c"),
            str(root / "native" / "src" / "o" / "o_core.c"),
            "-o",
            str(
                root
                / "src"
                / "demo"
                / "mm"
                / f"mm{sysconfig.get_config_var('EXT_SUFFIX')}"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src')\n"
            "from demo.mm import Sum\n"
            "print('type:', repr(Sum.__doc__))\n"
            "print('n:', repr(Sum.n.__doc__))\n"
            "print('mean:', repr(Sum.mean.__doc__))\n",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "type: 'Count and mean.'" in r.stdout, r.stdout
    assert "n: 'How many.'" in r.stdout, r.stdout
    assert "mean: 'The mean.'" in r.stdout, r.stdout
