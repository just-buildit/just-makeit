"""gh-646: a single-record result is a documented, named type on both faces.

A method declared ``single = true`` with ``result_fields`` returns one record.
Before this, the C descriptor passed ``NULL`` for the type doc and ``NULL`` for
every field doc — so ``help(ToneMetrics)`` was empty and ``ToneMetrics.enob``
carried no text — and the ``.pyi`` annotated the return as a bare
``tuple[float, float]``, which types unpacking but leaves ``r.enob`` unknown to
the type checker.

Three writers describe that one record: the C descriptor and the two ``.pyi``
generators (standalone and module-aggregated). They now share ``_record``, and
the tests below read all three, because a fix applied to one of these has twice
failed to reach the others.

The manifest wiring itself — a new key honoured by the scaffold and dropped by
``jm apply`` — is covered by ``tests/test_manifest_wiring_gate.py``'s ``record``
shape rather than repeated here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _record as R  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    DoxyBlock,
    member_doc_key,
)
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_FIELDS = [
    {"name": "enob", "type": "double", "doc": "Effective number of bits."},
    {"name": "sfdr_dbc", "type": "double", "doc": "SFDR, dBc."},
]


def _project(tmp_path: Path, module: str | None = None, **kw) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    if module:
        module_run(root, module)
    object_run(
        root,
        "meas",
        module,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    method_run(
        root,
        "meas",
        "measure",
        module,
        "float[]",
        "tone_metrics_t",
        False,
        [],
        result_fields=kw.pop("result_fields", [dict(f) for f in _FIELDS]),
        single=True,
        **kw,
    )
    return root


def _ext_c(root: Path, module: str | None = None) -> str:
    sub = module or "meas"
    return "".join(
        p.read_text(encoding="utf-8")
        for p in (root / "native" / "src" / sub).rglob("*.c")
    )


def _pyi(root: Path, module: str | None = None) -> str:
    rel = f"src/demo/{module}/{module}.pyi" if module else "src/demo/meas.pyi"
    return (root / rel).read_text(encoding="utf-8")


# ── the shape, unit level ───────────────────────────────────────────────────


class TestRecordShape:
    def test_manifest_doc_wins_over_the_header(self):
        m = {
            "result_fields": [
                {"name": "enob", "type": "double", "doc": "Declared."}
            ]
        }
        blocks = {member_doc_key("enob"): None}  # never consulted
        (f,) = R.fields(m, blocks)
        assert f.doc == "Declared."

    def test_a_field_falls_back_to_its_header_member_doc(self):
        """gh-671 already parses `///<`; a record field is one more reader."""
        m = {"result_fields": [{"name": "enob", "type": "double"}]}
        blocks = {member_doc_key("enob"): DoxyBlock(brief="From header.")}
        (f,) = R.fields(m, blocks)
        assert f.doc == "From header."

    def test_an_undocumented_field_has_no_doc(self):
        m = {"result_fields": [{"name": "enob", "type": "double"}]}
        assert R.fields(m, {})[0].doc == ""

    def test_the_type_doc_falls_back_to_a_synopsis_not_to_nothing(self):
        m = {
            "record_name": "ToneMetrics",
            "single": True,
            "result_fields": [dict(f) for f in _FIELDS],
        }
        assert R.type_doc(m, R.fields(m, {})) == "ToneMetrics(enob, sfdr_dbc)"

    def test_the_annotation_still_types_unpacking(self):
        """The named class must not cost what the bare tuple gave."""
        m = {"result_fields": [dict(f) for f in _FIELDS]}
        assert R.annotation(R.fields(m, {})) == "tuple[float, float]"


# ── the C face ──────────────────────────────────────────────────────────────


class TestRuntimeFace:
    def test_field_docs_reach_the_structseq_fields(self, tmp_path):
        c = _ext_c(_project(tmp_path, record_name="ToneMetrics"))
        assert '{"enob", "Effective number of bits."}' in c
        assert '{"sfdr_dbc", "SFDR, dBc."}' in c

    def test_the_record_doc_reaches_the_descriptor(self, tmp_path):
        root = _project(
            tmp_path,
            record_name="ToneMetrics",
            record_doc="Tone measurement results.",
        )
        assert '"meas.ToneMetrics", "Tone measurement results."' in _ext_c(
            root
        )

    def test_no_null_docs_remain(self, tmp_path):
        """The reported symptom, asserted directly."""
        c = _ext_c(_project(tmp_path, record_name="ToneMetrics"))
        assert '{"enob", NULL}' not in c
        assert '"meas.ToneMetrics", NULL' not in c

    def test_an_undocumented_record_still_gets_the_synopsis(self, tmp_path):
        root = _project(
            tmp_path,
            record_name="ToneMetrics",
            result_fields=[{"name": "enob", "type": "double"}],
        )
        c = _ext_c(root)
        assert '"meas.ToneMetrics", "ToneMetrics(enob)"' in c
        # ...and an undocumented FIELD keeps NULL rather than inventing prose.
        assert '{"enob", NULL}' in c

    def test_a_quote_in_a_doc_is_escaped(self, tmp_path):
        root = _project(
            tmp_path,
            record_name="ToneMetrics",
            result_fields=[
                {"name": "enob", "type": "double", "doc": 'The "good" bits.'}
            ],
        )
        assert '{"enob", "The \\"good\\" bits."}' in _ext_c(root)


# ── the Python face ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", [None, "dsp"], ids=["standalone", "module"])
class TestPythonFace:
    """Both .pyi generators, because only one of them used to be fixed."""

    def test_the_record_class_is_declared(self, tmp_path, module):
        pyi = _pyi(
            _project(
                tmp_path,
                module,
                record_name="ToneMetrics",
                record_doc="Tone measurement results.",
            ),
            module,
        )
        assert "class ToneMetrics(tuple[float, float]):" in pyi
        assert "Tone measurement results." in pyi

    def test_the_return_is_annotated_with_the_record(self, tmp_path, module):
        pyi = _pyi(
            _project(tmp_path, module, record_name="ToneMetrics"), module
        )
        assert "-> ToneMetrics:" in pyi
        assert "-> tuple[float, float]:" not in pyi

    def test_each_field_is_typed_and_documented(self, tmp_path, module):
        pyi = _pyi(
            _project(tmp_path, module, record_name="ToneMetrics"), module
        )
        assert "    def enob(self) -> float:" in pyi
        assert '"""Effective number of bits."""' in pyi
        assert "    def sfdr_dbc(self) -> float:" in pyi

    def test_an_undocumented_field_is_a_plain_stub(self, tmp_path, module):
        pyi = _pyi(
            _project(
                tmp_path,
                module,
                record_name="ToneMetrics",
                result_fields=[{"name": "enob", "type": "double"}],
            ),
            module,
        )
        # No prose synthesised from the field's own name.
        assert "    def enob(self) -> float: ..." in pyi

    def test_the_record_class_precedes_the_class_that_returns_it(
        self, tmp_path, module
    ):
        """A forward reference in a stub is legal but reads badly; order it."""
        pyi = _pyi(
            _project(tmp_path, module, record_name="ToneMetrics"), module
        )
        assert pyi.index("class ToneMetrics") < pyi.index("class Meas")

    def test_the_stub_is_valid_python(self, tmp_path, module):
        import ast

        pyi = _pyi(
            _project(tmp_path, module, record_name="ToneMetrics"), module
        )
        ast.parse(pyi)


def test_a_view_only_record_method_declares_its_record(tmp_path):
    """A view's own methods are stubbed separately from its parent's.

    The record classes are collected once for the whole module, so a method
    that exists only on a view would otherwise annotate `-> NPRMetrics` with no
    NPRMetrics declared anywhere in the stub -- an undefined name, which is the
    one way this feature can produce a broken `.pyi` rather than a thin one.
    """
    from just_makeit._view import run as view_run

    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "dsp")
    object_run(
        root,
        "meas",
        "dsp",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    view_run(root, "meas", "Burst", "dsp", create_fn="meas_create_burst")
    method_run(
        root,
        "meas",
        "analyze",
        "dsp",
        "float[]",
        "npr_metrics_t",
        False,
        [],
        result_fields=[
            {"name": "npr_db", "type": "double", "doc": "NPR, dB."}
        ],
        single=True,
        record_name="NPRMetrics",
        view="Burst",
    )
    apply_run(root)
    pyi = _pyi(root, "dsp")
    assert "class NPRMetrics(tuple[float]):" in pyi
    assert "-> NPRMetrics:" in pyi


# ── the manifest ────────────────────────────────────────────────────────────


class TestManifest:
    def test_the_field_doc_survives_the_fast_dump(self):
        """`_dump` is the scratch fast path; its inline table grew a key.

        The apply-replay self-check falls back to tomlkit when `_dump` is not
        faithful, so a dropped key here is invisible end-to-end and only shows
        up as a silent loss of the fast path. Pin the serializer directly.
        """
        cfg = {
            "project": {"name": "demo"},
            "meas": {
                "arg_type": "float",
                "return_type": "float",
                "methods": [
                    {
                        "name": "measure",
                        "arg_type": "float[]",
                        "return_type": "tone_metrics_t",
                        "single": True,
                        "result_fields": [dict(f) for f in _FIELDS],
                    }
                ],
            },
        }
        text = C._dump(cfg)
        assert 'doc = "Effective number of bits."' in text

    def test_a_free_function_record_field_dumps_its_doc_too(self):
        """The second emit site — the one a one-place fix would have missed."""
        cfg = {
            "project": {"name": "demo"},
            "module": {
                "dsp": {
                    "functions": [
                        {
                            "name": "measure",
                            "result_fields": [dict(f) for f in _FIELDS],
                        }
                    ]
                }
            },
        }
        assert 'doc = "Effective number of bits."' in C._dump(cfg)


def test_a_project_without_a_record_is_unchanged(tmp_path):
    """Zero churn: the new stub slot must be empty when nothing declares one."""
    root = tmp_path / "plain"
    new_run("plain", root)
    object_run(
        root,
        "widget",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    apply_run(root)
    pyi = (root / "src/plain/widget.pyi").read_text(encoding="utf-8")
    assert "tuple[" not in pyi
    assert pyi.startswith("from typing import Any, final")
