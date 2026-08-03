"""gh-720: `jm script` dropped every key of the single-record shape.

`just-makeit script` reconstructs the CLI history from ``just-makeit.toml``,
which makes it a *third* writer over the same manifest that `jm <cmd>` and
`jm apply` write. It had no handling for the record shape at all, so a
replayed ``jm method`` line came back returning a bare scalar instead of a
named ``PyStructSequence`` — the gh-490 silent-divergence trap (a
reconstruction that quietly differs from the original is worse than one that
fails loudly), one shape further on.

Pre-existing since gh-244, so five flags were affected: ``--result-field``
(gh-244), ``--single`` (gh-244), ``--record-name`` (gh-257),
``--record-module`` (gh-261) and ``--record-doc`` (gh-646).

The registration-free half lives in ``test_manifest_wiring_gate.py``, which
now replays the emitted script and compares manifests; this file pins the
individual flags, and covers ``jm function``'s ``--result-field`` — the
function shape is not in ``SHAPES``, so the gate does not reach it.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _script  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_FIELDS = [
    {"name": "enob", "type": "double", "doc": "Effective bits."},
    {"name": "sfdr_dbc", "type": "double"},
]


def _emit(root: Path) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _script.run(root)
    return buf.getvalue()


@pytest.fixture
def project(tmp_path):
    """A record method carrying all five keys, plus a record function."""
    root = tmp_path / "demo"
    with redirect_stdout(io.StringIO()):
        new_run("demo", root)
        object_run(
            root,
            "meas",
            None,
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        method_run(
            root,
            "meas",
            "measure",
            None,
            "float[]",
            "tone_metrics_t",
            False,
            [],
            result_fields=_FIELDS,
            single=True,
            record_name="ToneMetrics",
            record_module="demo.meas",
            record_doc="Tone measurement results.",
        )
        module_run(root, "dsp")
        function_run(
            root,
            "peaks",
            "dsp",
            params=[("x", "float[]")],
            result_fields=[
                {"name": "idx", "type": "int"},
                {"name": "val", "type": "double"},
            ],
        )
    return root


class TestEmittedFlags:
    """Every key the record shape can hold reaches the emitted script."""

    @pytest.mark.parametrize(
        "flag",
        [
            '--result-field "enob:double:Effective bits."',
            "--result-field sfdr_dbc:double",
            "--single",
            "--record-name ToneMetrics",
            "--record-module demo.meas",
            '--record-doc "Tone measurement results."',
        ],
    )
    def test_method_flag_is_emitted(self, project, flag):
        assert flag in _emit(project)

    def test_function_result_fields_are_emitted(self, project):
        # `jm function` takes --result-field but not --single; dropping the
        # fields rebuilt the function returning its bare --return-type.
        out = _emit(project)
        assert "--result-field idx:int" in out
        assert "--result-field val:double" in out

    def test_a_field_doc_survives_a_colon(self):
        # parse_result_field splits at most twice, so a doc may contain ':'.
        # It must not be quoted away or truncated on the way out.
        spec = _script._record_flags(
            {"result_fields": [{"name": "n", "type": "int", "doc": "a:b"}]}
        )
        assert spec == ["    --result-field n:int:a:b \\\n"]

    def test_no_record_keys_emits_nothing(self):
        assert _script._record_flags({"name": "plain"}) == []


class TestRoundTrip:
    """The emitted flags parse back to the manifest they came from."""

    def test_reparsing_yields_the_original_fields(self, project):
        """The emitted spec is what the CLI parser accepts, character for
        character — including the per-field doc, which is the component the
        naive ``name:type`` emitter would have lost."""
        import shlex

        from just_makeit import _record

        cfg = C.load(project)
        original = C.methods(cfg, "meas")[0]["result_fields"]

        specs: list[str] = []
        for line in _emit(project).splitlines():
            toks = shlex.split(line.rstrip("\\ "))
            if len(toks) == 2 and toks[0] == "--result-field":
                specs.append(toks[1])

        # The function's two fields are emitted too; the method's come first.
        parsed = [_record.parse_result_field(s) for s in specs]
        assert parsed[: len(original)] == original
        assert {"name": "idx", "type": "int"} in parsed
