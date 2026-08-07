"""The gate for a recurring class: a new key that is not wired everywhere.

Four times in one release cycle a manifest key or generated docstring worked
through the CLI and was then silently dropped by the flow that regenerates from
the manifest, or landed on one face and not the other:

===========================  ==================================================
`count_default` (gh-663)     honoured by `jm method`, dropped by `jm apply`
`[module.X] doc` (gh-645)    same -- the replay did not forward it
`tp_doc` (gh-681)            `.pyi` derived it, the runtime kept a literal
accessors/`max_out` (gh-684) both faces canned, and disagreeing with each other
===========================  ==================================================

None of these were subtle once looked for, and all four have the same two cheap
detectors, which is what this module automates:

1. **`jm status == 0` on a freshly scaffolded project, and again after
   `jm apply`.** A key the scaffold honours and the replay drops makes apply's
   output differ from the scaffold's, so the project reports STALE against
   itself. This half needs **no per-key registration**: it trips for a key
   nobody thought to add here, as long as the shape is in `SHAPES`.
2. **A declared docstring must appear on _both_ faces.** Reading one face is
   what made gh-676 and gh-644 look like they contradicted each other when both
   were right about different cells, and what made gh-685 look like a two-face
   bug when only the stub was affected.

Adding a new manifest key? Add a shape below that exercises it. Adding a new
documentable surface? Add a row to `DOC_SURFACES`. Neither is required for the
idempotence half to protect you, which is the point.
"""

from __future__ import annotations

import hashlib
import io
import shlex
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._cli import main as cli_main  # noqa: E402
from just_makeit._script import run as script_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402

# ── shapes ──────────────────────────────────────────────────────────────────
# Each builds a project exercising a different slice of the manifest. Keep them
# cheap: this file is about wiring, not behaviour, so nothing is compiled.


def _base(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    return root


def _standalone(tmp_path: Path) -> Path:
    root = _base(tmp_path)
    object_run(
        root,
        "widget",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return root


def _module(tmp_path: Path) -> Path:
    root = _base(tmp_path)
    module_run(root, "filt", doc="Filter bank.")
    object_run(
        root,
        "widget",
        "filt",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return root


def _module_extras(tmp_path: Path) -> Path:
    """The `[module.X]` keys beyond `doc` (gh-720).

    `jm script` emitted the module command bare, so every one of these was
    lost on replay. They are cheap to declare and no other shape reaches them,
    which is exactly why they went unnoticed -- an emitter branch no shape
    exercises is one nothing proves correct.
    """
    root = _base(tmp_path)
    module_run(
        root,
        "filt",
        extra_include_dirs=["${DOPPLER_INCLUDE_DIR}"],
        extra_link_libs=["PkgConfig::DOPPLER"],
        extra_types=["HalfbandDecimatorDp"],
        functions_in_core=True,
        doc="Filter bank.",
    )
    object_run(
        root,
        "widget",
        "filt",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    return root


def _renamed_class(tmp_path: Path) -> Path:
    """A component named for its C prefix, wearing a chosen Python class name.

    gh-808. `class_name` was emitted by `jm script` for VIEWS only, so a
    replayed object came back as the CamelCased component (`DpTlm`) instead of
    the declared class. No shape reached it -- an emitter branch no shape
    exercises is one nothing proves correct, which is this file's own thesis.

    It is also the exact shape gh-805 §A documents as the supported way to
    adopt existing C: name the component after the C prefix and keep the
    Python face with `class_name`. Every project following that advice had a
    `jm script` that rebuilt a different project.
    """
    root = _base(tmp_path)
    module_run(root, "telemetry")
    object_run(
        root,
        "dp_tlm",
        "telemetry",
        state_vars=[("cap", "size_t", "64")],
        arg_type="float",
        return_type="float",
        class_name="Telemetry",
    )
    return root


def _serializable(tmp_path: Path) -> Path:
    root = _base(tmp_path)
    object_run(
        root,
        "widget",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        serializable=True,
    )
    return root


def _variable_output(tmp_path: Path) -> Path:
    """Exercises count_default (gh-657/663) and max_out (gh-684)."""
    root = _base(tmp_path)
    object_run(
        root,
        "fir",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    method_run(
        root, "fir", "execute", None, "float[]", "float", True, [], max_out=4
    )
    return root


def _status_return(tmp_path: Path) -> Path:
    """Exercises status_return + its error/error_message pair (gh-823 Ask D).

    `status_return` was forwarded by `_apply` and emitted by neither the CLI
    nor `jm script`, so a replayed project came back without it — silently,
    because no shape here exercised the key. That is the same hole gh-808 fell
    through, and this arm is what closes it.
    """
    root = _base(tmp_path)
    object_run(
        root,
        "cap",
        None,
        state_vars=[("n", "size_t", "0")],
        arg_type="float",
        return_type="float",
    )
    method_run(
        root,
        "cap",
        "close",
        None,
        "void",
        "int",
        False,
        [],
        status_return=True,
        error="RuntimeError",
        error_message="records were dropped; the caller broke the contract",
    )
    return root


def _view(tmp_path: Path) -> Path:
    root = _base(tmp_path)
    module_run(root, "dsp")
    object_run(
        root,
        "ddc",
        "dsp",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    method_run(
        root, "ddc", "execute_ctrl", "dsp", "float[]", "float", False, []
    )
    view_run(root, "ddc", "MatchedDDC", "dsp", create_fn="ddc_create_matched")
    return root


def _record(tmp_path: Path) -> Path:
    """Exercises result_fields `doc` + `record_doc` (gh-646).

    Both are new manifest keys on the single-record shape, and both are read by
    three writers (the C descriptor and the two .pyi generators). A key the
    scaffold honours and `_dump` drops makes the replay disagree with the
    scaffold, which is what the idempotence half below detects.
    """
    root = _base(tmp_path)
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
        result_fields=[
            {"name": "enob", "type": "double", "doc": "Effective bits."},
            {"name": "sfdr_dbc", "type": "double", "doc": "SFDR, dBc."},
        ],
        single=True,
        record_name="ToneMetrics",
        record_doc="Tone measurement results.",
    )
    return root


SHAPES = {
    "standalone": _standalone,
    "module": _module,
    "module_extras": _module_extras,
    "renamed_class": _renamed_class,
    "serializable": _serializable,
    "variable_output": _variable_output,
    "status_return": _status_return,
    "view": _view,
    "record": _record,
}


def _tree_digest(root: Path) -> dict[str, str]:
    """Content hash per generated file, so a diff names the file that moved."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = str(p.relative_to(root))
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=sorted(SHAPES))
class TestScaffoldAndReplayAgree:
    """The half that needs no registration -- it protects unknown keys too."""

    def test_fresh_scaffold_reports_no_drift(self, tmp_path, shape):
        root = SHAPES[shape](tmp_path)
        assert status_run(root) == 0, (
            f"a freshly scaffolded {shape} project is STALE against itself. "
            "The scaffold and `jm apply` render the same file differently -- "
            "usually a manifest key the scaffold honours and the replay drops, "
            "or a value one path derives and the other does not."
        )

    def test_apply_changes_nothing(self, tmp_path, shape):
        root = SHAPES[shape](tmp_path)
        before = _tree_digest(root)
        apply_run(root)
        after = _tree_digest(root)
        moved = sorted(k for k in before if before[k] != after.get(k))
        assert not moved, (
            f"`jm apply` rewrote {moved} on a {shape} project that was already "
            "up to date. Whatever differs is generated from the manifest by "
            "one path and not the other."
        )

    def test_apply_is_a_fixed_point(self, tmp_path, shape):
        # A second apply must also be a no-op: a key that only survives one
        # round trip is still broken, just less obviously.
        root = SHAPES[shape](tmp_path)
        apply_run(root)
        digest = _tree_digest(root)
        apply_run(root)
        assert _tree_digest(root) == digest
        assert status_run(root) == 0


# ── the third writer ────────────────────────────────────────────────────────
# `jm script` reconstructs the CLI history from the manifest, which makes it a
# third writer over the same keys — and the two arms above cannot see it, since
# neither the scaffold nor `jm apply` ever reads its output. gh-720: it emitted
# no record flags at all, so a replayed `--single` method came back returning a
# bare scalar. Like the idempotence arm, this needs **no per-key registration**:
# a key dropped from the emitted script shows up as a manifest that differs
# after the round trip, whoever added it.


def _commands(script: str) -> list[list[str]]:
    """Split an emitted script into argv lists, honouring `\\` continuations.

    `_render_cmd` is the only thing that writes these, so the grammar is
    exactly: comments, blank lines, one `cd`, and `just-makeit` commands whose
    flags are continued with a trailing backslash and quoted by `_script._q`.
    `shlex` undoes that quoting the same way `sh` would.
    """
    out: list[list[str]] = []
    buf = ""
    for line in script.splitlines():
        s = line.strip()
        if not buf and (not s or s.startswith("#")):
            continue
        if s.endswith("\\"):
            buf += s[:-1] + " "
            continue
        out.append(shlex.split(buf + s))
        buf = ""
    assert not buf, "the emitted script ends mid-continuation"
    return out


def _replay(script: str, dest: Path, monkeypatch) -> Path:
    """Run an emitted script in-process, returning the rebuilt project root."""
    dest.mkdir(parents=True, exist_ok=True)
    cwd = dest
    for argv in _commands(script):
        if argv[0] == "cd":
            cwd = cwd / argv[1]
            continue
        assert argv[0] == "just-makeit", f"unexpected command: {argv}"
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(sys, "argv", argv)
        with redirect_stdout(io.StringIO()):
            try:
                cli_main()
            except SystemExit as exc:
                assert not exc.code, (
                    f"the emitted script does not run: `{shlex.join(argv)}` "
                    f"exited {exc.code}."
                )
    return cwd


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=sorted(SHAPES))
def test_script_round_trips_the_manifest(tmp_path, shape, monkeypatch):
    root = SHAPES[shape](tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        script_run(root)

    rebuilt = _replay(buf.getvalue(), tmp_path / "replay", monkeypatch)

    before, after = C.load(root), C.load(rebuilt)
    assert after == before, (
        f"`jm script` does not round-trip a {shape} project. The emitted "
        "script rebuilds a DIFFERENT manifest, which is worse than failing "
        "loudly — usually a key `jm method`/`jm object` accepts that "
        "`_script.py` never learned to emit.\n"
        f"emitted script:\n{buf.getvalue()}"
    )


# ── both faces ──────────────────────────────────────────────────────────────
# Each row: a sentinel, how to declare it, and that it must reach BOTH the
# generated `.pyi` and the generated C. Reading one face is what made gh-676
# and gh-644 look contradictory.


def _faces(root: Path) -> tuple[str, str]:
    """``(python_face, runtime_face)`` -- everything a reader of each sees.

    The Python face is not just the ``.pyi``: a module's documentation lands on
    the re-export ``__init__.py`` (gh-645), which is what griffe reads for the
    module page. Globbing only ``*.pyi`` here would have reported a false gap
    for module docs -- the kind of one-face reading this gate exists to stop.
    """
    py = "".join(
        p.read_text(encoding="utf-8")
        for pat in ("*.pyi", "__init__.py")
        for p in (root / "src").rglob(pat)
    )
    c = "".join(
        p.read_text(encoding="utf-8") for p in (root / "native").rglob("*.c")
    )
    return py, c


def _declare_header_brief(root: Path, comp: str, old: str, new: str) -> None:
    h = root / "native" / "inc" / comp / f"{comp}_core.h"
    t = h.read_text(encoding="utf-8")
    assert old in t, f"the scaffold no longer writes {old!r}"
    h.write_text(t.replace(old, new), encoding="utf-8")


def _shape_create(tmp_path):
    root = _standalone(tmp_path)
    _declare_header_brief(
        root, "widget", "@brief Create a widget instance.", "@brief SENTINEL0."
    )
    return root


def _shape_reset(tmp_path):
    root = _standalone(tmp_path)
    _declare_header_brief(
        root, "widget", "@brief Reset Widget to", "@brief SENTINEL1 reset"
    )
    return root


def _shape_step(tmp_path):
    root = _standalone(tmp_path)
    _declare_header_brief(
        root, "widget", "@brief Process one input sample.", "@brief SENTINEL2."
    )
    return root


def _shape_accessor(tmp_path):
    root = _standalone(tmp_path)
    _declare_header_brief(
        root, "widget", "@brief Get current gain.", "@brief SENTINEL3."
    )
    return root


def _shape_module_doc(tmp_path):
    root = _base(tmp_path)
    module_run(root, "filt", doc="SENTINEL4.")
    object_run(
        root,
        "widget",
        "filt",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    return root


def _shape_property_doc(tmp_path):
    root = _standalone(tmp_path)
    cfg = C.load(root)
    cfg.setdefault("widget", {}).setdefault("properties", []).append(
        {"name": "note", "type": "int", "doc": "SENTINEL5."}
    )
    C.save(root, cfg)
    apply_run(root)
    return root


def _shape_record_field_doc(tmp_path):
    root = _record(tmp_path)
    cfg = C.load(root)
    cfg["meas"]["methods"][0]["result_fields"][0]["doc"] = "SENTINEL6."
    C.save(root, cfg)
    return root


def _shape_record_doc(tmp_path):
    root = _record(tmp_path)
    cfg = C.load(root)
    cfg["meas"]["methods"][0]["record_doc"] = "SENTINEL7."
    C.save(root, cfg)
    return root


DOC_SURFACES = {
    "create_brief": (_shape_create, "SENTINEL0."),
    "reset_brief": (_shape_reset, "SENTINEL1"),
    "step_brief": (_shape_step, "SENTINEL2."),
    "accessor_brief": (_shape_accessor, "SENTINEL3."),
    "module_doc": (_shape_module_doc, "SENTINEL4."),
    "property_doc": (_shape_property_doc, "SENTINEL5."),
    # gh-646: a record's field docs and the record type's own doc must reach
    # PyStructSequence_Field / _Desc *and* the declared .pyi record class.
    "record_field_doc": (_shape_record_field_doc, "SENTINEL6."),
    "record_doc": (_shape_record_doc, "SENTINEL7."),
}


@pytest.mark.parametrize(
    "surface", sorted(DOC_SURFACES), ids=sorted(DOC_SURFACES)
)
def test_declared_doc_reaches_both_faces(tmp_path, surface):
    build, sentinel = DOC_SURFACES[surface]
    root = build(tmp_path)
    apply_run(root)
    py, c = _faces(root)
    missing = [
        face
        for face, text in (("python", py), ("runtime", c))
        if sentinel not in text
    ]
    assert not missing, (
        f"the {surface} documentation reached "
        f"{'neither face' if len(missing) == 2 else 'only one face'} "
        f"(missing from: {', '.join(missing)}). The two faces are generated by "
        "different builders; a fix applied to one does not reach the other."
    )
