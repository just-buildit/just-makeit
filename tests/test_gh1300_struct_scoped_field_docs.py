"""A struct field's doc documents that struct's field, and no other.

gh-1300. A field's trailing ``/**<`` doc was looked up by bare NAME across the
component's header and everything it includes, so doppler's
``DsssBurstReceiver.dropped`` was documented as "Overrun ctr." -- the ring
buffer's ``dropped``, in an unrelated header -- on both faces, which agreed and
so passed every parity gate. Editing a comment in ``buffer.h`` changed the
documentation of a DSSS receiver.

Every struct-field face now reads from ONE struct:

| reader | struct |
| --- | --- |
| a property | the component's ``<comp>_state_t`` |
| a state ``get_``/``set_`` accessor | the component's ``<comp>_state_t`` |
| a record field | the record's own struct (``record_dtype``, or a ``single`` record's return type) |

and a field that struct does not document gets no derived doc: an undocumented
member is a gap the coverage meter counts, a stranger's sentence is a wrong
answer nothing counts. Enumerators keep the bare-name lookup, because C makes
them unique.

Each fixture puts a same-named DONOR field, documented, in a header the
component includes and ahead of the true owner, so a first-match-wins lookup
picks the donor.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402
from just_makeit import _incpath as INC  # noqa: E402

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

_DONOR = "DONOR_SENTENCE"


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _inc(root: Path, name: str) -> str:
    """An ``#include`` of the project's own header *name*, in its layout."""
    return f'#include "{INC.include(name, root)}"'


def _include_donor(root: Path, comp: str) -> None:
    """A shared header with a documented same-named field, included first."""
    _write(
        root,
        INC.rel("ring/ring_core.h", root),
        "typedef struct {\n"
        f"    size_t dropped;  /**< {_DONOR} ring. */\n"
        f"    double snr;      /**< {_DONOR} snr. */\n"
        "} ring_t;\n",
    )
    h = root / INC_ROOT / comp / f"{comp}_core.h"
    t = h.read_text(encoding="utf-8")
    assert t.count(_inc(root, "clib_common.h")) == 1
    h.write_text(
        t.replace(
            _inc(root, "clib_common.h"),
            _inc(root, "clib_common.h")
            + "\n"
            + _inc(root, "ring/ring_core.h"),
        ),
        encoding="utf-8",
    )


def _faces(root: Path) -> str:
    """Every generated doc face in the tree, as one blob."""
    blobs = [
        p.read_text(encoding="utf-8")
        for pat in ("src/**/*.pyi", "native/src/**/*_ext*.c")
        for p in root.glob(pat)
    ]
    assert blobs
    return "\n".join(blobs)


def _object(root: Path, module: str | None) -> None:
    _quiet(new_run, "demo", root)
    if module:
        _quiet(module_run, root, module)
    _quiet(
        object_run,
        root,
        "rx",
        module,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )


FACES = [pytest.param(None, id="standalone"), pytest.param("m", id="module")]


# ── properties ──────────────────────────────────────────────────────────────


def _property_project(tmp_path: Path, module: str | None, own: str) -> Path:
    """`rx.dropped`, a field property; *own* is its own field's comment."""
    root = tmp_path / "demo"
    _object(root, module)
    _quiet(
        property_run,
        root,
        "rx",
        "dropped",
        module,
        "uint64_t",
        False,
        field=True,
    )
    h = root / INC_ROOT / "rx" / "rx_core.h"
    t = h.read_text(encoding="utf-8")
    decl = "uint64_t dropped;"
    assert t.count(decl) == 1, t
    h.write_text(t.replace(decl, f"{decl} {own}"), encoding="utf-8")
    _include_donor(root, "rx")
    _quiet(apply_run, root)
    return root


@pytest.mark.parametrize("module", FACES)
def test_a_property_never_takes_a_strangers_field_doc(tmp_path, module):
    """The reported case: the owner is silent, a stranger is not."""
    root = _property_project(tmp_path, module, own="")
    assert _DONOR not in _faces(root)


@pytest.mark.parametrize("module", FACES)
def test_a_property_still_takes_its_own_field_doc(tmp_path, module):
    """gh-671's feature, which scoping must not cost."""
    root = _property_project(
        tmp_path, module, own="/**< OWN_SENTENCE a lost burst each. */"
    )
    blob = _faces(root)
    assert "OWN_SENTENCE" in blob
    assert _DONOR not in blob


# ── state accessors ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", FACES)
def test_a_state_accessor_never_takes_a_strangers_field_doc(tmp_path, module):
    """`get_snr()` over a state field `snr` the component leaves silent."""
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    if module:
        _quiet(module_run, root, module)
    _quiet(
        object_run,
        root,
        "rx",
        module,
        state_vars=[("snr", "double", "0.0")],
        arg_type="float",
        return_type="float",
    )
    _include_donor(root, "rx")
    _quiet(apply_run, root)
    blob = _faces(root)
    assert "get_snr" in blob
    assert _DONOR not in blob


# ── records ─────────────────────────────────────────────────────────────────


def _record_project(tmp_path: Path, module: str | None) -> Path:
    """`rx.measure()` returns `meas_t` by value; the donor says `snr` too."""
    root = tmp_path / "demo"
    _object(root, module)
    _quiet(
        method_run,
        root,
        "rx",
        "measure",
        module,
        "float[]",
        "meas_t",
        False,
        [],
        result_fields=[{"name": "snr", "type": "double"}],
        single=True,
        record_name="Meas",
    )
    if module:
        # A second object AFTER rx: the module stub merges both objects'
        # doc maps, and the record is rx's.
        _quiet(
            object_run,
            root,
            "tx",
            module,
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
    _include_donor(root, "rx")
    if module:
        # ...and tx documents a struct of its own, so its doc map carries the
        # per-struct key too. Without one, a spread keeps rx's by accident
        # and the merge test cannot fail -- measured, it passed that way.
        _include_donor(root, "tx")
    _write(
        root,
        INC.rel("meas/meas_core.h", root),
        "typedef struct {\n    double snr;  /**< OWN_SNR in dB. */\n} meas_t;\n",
    )
    h = root / INC_ROOT / "rx" / "rx_core.h"
    h.write_text(
        h.read_text(encoding="utf-8").replace(
            _inc(root, "ring/ring_core.h"),
            _inc(root, "ring/ring_core.h")
            + "\n"
            + _inc(root, "meas/meas_core.h"),
        ),
        encoding="utf-8",
    )
    _quiet(apply_run, root)
    return root


@pytest.mark.parametrize("module", FACES)
def test_a_record_field_reads_its_own_struct(tmp_path, module):
    blob = _faces(_record_project(tmp_path, module))
    assert "OWN_SNR" in blob
    assert _DONOR not in blob


def test_a_module_record_keeps_its_docs_beside_a_later_object(tmp_path):
    """The merge: one key holds every struct, so a spread keeps the LAST
    object's structs and drops the record's own. Measured on doppler:
    `ReceiverStatus` lost all thirteen field docs that way."""
    pyi = (
        _record_project(tmp_path, "m") / "src" / "demo" / "m" / "m.pyi"
    ).read_text(encoding="utf-8")
    assert "OWN_SNR" in pyi
