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
from _jminc import INC_ROOT  # noqa: E402

import ast
import hashlib
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from _jmrun import run_cli, script_round_trip

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
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


def _renamed_standalone(tmp_path: Path) -> Path:
    """`class_name` on a STANDALONE object (gh-1651).

    `_renamed_class` covers the key on a module object only, and a standalone
    object renders through a different path: `apply`'s post-replay re-render
    in `_sync_aggregates`, whose context seeded the default class name. The
    fresh scaffold was STALE against itself, `apply` then registered the type
    as `Named` while `__init__.py` imported `Renamed`, and every later
    `status` agreed. :func:`test_every_object_key_is_exercised_in_both_placements`
    is what keeps a key from being covered in one placement only again.
    """
    root = _base(tmp_path)
    object_run(
        root,
        "named",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        class_name="Renamed",
    )
    return root


def _serializable_module(tmp_path: Path) -> Path:
    """`serializable` on a module object -- the placement `_serializable` does
    not reach (gh-1651's placement coverage)."""
    root = _base(tmp_path)
    module_run(root, "store")
    object_run(
        root,
        "widget",
        "store",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        serializable=True,
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


def _capsule_typed(tmp_path: Path, module: str | None) -> Path:
    """A capsule producer that SAYS what its pointer is, and its consumer.

    gh-2215: `_replay_property` forwarded every property key but
    `capsule_type` (gh-1235), so the replay's manifest held a producer
    publishing over `expr` with no declared type. The consumer's `object`
    reference resolves against that manifest, and `resolve_object_ref`
    refuses exactly that producer -- so on a module consumer `jm apply`
    refused a project `jm property` had built.

    The consumer is built the way `jm object --init-param src:object:nco`
    builds it, which crashed on `_CTYPE_META['']` until the creation path
    resolved the reference (gh-1893) -- and `jm script` then has to emit the
    producer's capsule before the consumer that names it.
    """
    from just_makeit._cli_parse import parse_init_param_flag

    root = _base(tmp_path)
    if module:
        module_run(root, module)
    object_run(
        root,
        "nco",
        module,
        state_vars=[("cap", "size_t", "16")],
        arg_type="float",
        return_type="float",
    )
    property_run(
        root,
        "nco",
        "_capsule",
        module,
        "capsule",
        False,
        capsule="demo.nco.desc",
        capsule_type="const nco_desc_t *",
        expr="&self->handle->cap",
    )
    src, _ = parse_init_param_flag(["--init-param", "src:object:nco"], 0)
    object_run(
        root,
        "mixer",
        module,
        arg_type="float",
        return_type="float",
        init_params=[src],
    )
    return root


def _capsule_typed_standalone(tmp_path: Path) -> Path:
    return _capsule_typed(tmp_path, None)


def _capsule_typed_module(tmp_path: Path) -> Path:
    return _capsule_typed(tmp_path, "osc")


def _hand_ordered(root: Path, cfg: dict) -> Path:
    """Save *cfg* as a hand-written manifest and `apply` it.

    The file is written fresh so its section order is *cfg*'s, which is the
    point: the shapes below declare an order no sequence of commands
    writes, the way an author editing the TOML does.
    """
    (root / C.FILENAME).unlink()
    C.save(root, cfg)
    with redirect_stdout(io.StringIO()):
        apply_run(root)
    return root


def _consumer_first_standalone(tmp_path: Path) -> Path:
    """gh-2215: the consumer's section above its producer's.

    `jm script` emitted objects in manifest order, so the consumer's
    `jm object` line ran before the producer existed and was refused.
    """
    root = _capsule_typed(tmp_path, None)
    cfg = C.load(root)
    first = ["project", "mixer"]
    return _hand_ordered(
        root,
        {k: cfg[k] for k in [*first, *(k for k in cfg if k not in first)]},
    )


def _consumer_first_module(tmp_path: Path) -> Path:
    """gh-2215: a module listing the consumer before its producer.

    The `objects` list is the module's load order (gh-1985), so the script
    cannot create the producer first and append: it creates it first and
    places the consumer with `jm object --before`.
    """
    root = _capsule_typed(tmp_path, "osc")
    cfg = C.load(root)
    cfg["module"]["osc"]["objects"] = ["mixer", "nco"]
    return _hand_ordered(root, cfg)


def _view_target(tmp_path: Path) -> Path:
    """gh-2215: an `object` init-param naming a VIEW of the producer.

    The reference resolves only once the view is declared, and `jm script`
    emitted every view after every object.
    """
    from just_makeit._cli_parse import parse_init_param_flag

    root = _base(tmp_path)
    module_run(root, "osc")
    object_run(
        root,
        "nco",
        "osc",
        state_vars=[("cap", "size_t", "16")],
        arg_type="float",
        return_type="float",
    )
    property_run(
        root,
        "nco",
        "_capsule",
        "osc",
        "capsule",
        False,
        capsule="demo.nco.desc",
        capsule_type="const nco_desc_t *",
        expr="&self->handle->cap",
    )
    view_run(root, "nco", "NcoDesc", "osc", create_fn="nco_create_desc")
    src, _ = parse_init_param_flag(
        ["--init-param", "src:object:nco.NcoDesc"], 0
    )
    object_run(
        root,
        "mixer",
        None,
        arg_type="float",
        return_type="float",
        init_params=[src],
    )
    return root


def _object_flags(tmp_path: Path, module: str | None) -> Path:
    """Object keys the replay's `_object.run` call never named (gh-2215).

    `step_delegates_to_steps` and `opaque_state` are written by
    `jm object --step-delegates` / `--opaque-state`, and `bench = false` by
    `jm method --no-bench`. The replay forwarded none of the three; this
    shape is what measures whether that changed a render.
    """
    root = _base(tmp_path)
    if module:
        module_run(root, module)
    object_run(
        root,
        "deleg",
        module,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        step_delegates=True,
    )
    object_run(
        root,
        "opaq",
        module,
        state_vars=[],
        arg_type="float",
        return_type="float",
        no_step=True,
        opaque_state=True,
    )
    method_run(
        root,
        "deleg",
        "scale",
        module,
        "float",
        "float",
        False,
        [],
        no_bench=True,
    )
    return root


def _object_flags_standalone(tmp_path: Path) -> Path:
    return _object_flags(tmp_path, None)


def _object_flags_module(tmp_path: Path) -> Path:
    return _object_flags(tmp_path, "blk")


def _project_deps(tmp_path: Path) -> Path:
    """`[project]` dependency keys `jm new` writes (gh-2215).

    The replay's `_new.run` call named none of `find_packages`,
    `pkg_modules` or `c_deps`.
    """
    root = tmp_path / "demo"
    new_run(
        "demo",
        root,
        find_packages=["ZLIB"],
        pkg_modules=["fftw3"],
        c_deps=["vendored"],
    )
    object_run(
        root,
        "widget",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    return root


SHAPES = {
    "object_flags_standalone": _object_flags_standalone,
    "object_flags_module": _object_flags_module,
    "project_deps": _project_deps,
    "capsule_typed_standalone": _capsule_typed_standalone,
    "capsule_typed_module": _capsule_typed_module,
    "consumer_first_standalone": _consumer_first_standalone,
    "consumer_first_module": _consumer_first_module,
    "view_target": _view_target,
    "standalone": _standalone,
    "module": _module,
    "module_extras": _module_extras,
    "renamed_class": _renamed_class,
    "renamed_standalone": _renamed_standalone,
    "serializable": _serializable,
    "serializable_module": _serializable_module,
    "variable_output": _variable_output,
    "status_return": _status_return,
    "view": _view,
    "record": _record,
}


#: Object-level keys that exist in ONE placement only, by design -- each with
#: the reason. Everything else a shape sets must be exercised on a standalone
#: object AND a module object.
ONE_PLACEMENT = {
    "views": "a module-object feature: `jm view` refuses without --module",
}


def _keys_by_placement() -> "dict[str, set[str]]":
    """Every object-level key the SHAPES really set, by where its object sits.

    Read from the manifests the shapes produce, not from a list: a key a new
    shape sets is covered without being registered here.
    """
    import tempfile

    out: "dict[str, set[str]]" = {"standalone": set(), "module": set()}
    for fn in SHAPES.values():
        with tempfile.TemporaryDirectory() as d:
            with redirect_stdout(io.StringIO()):
                cfg = C.load(fn(Path(d)))
            for comp in C.components(cfg):
                place = "module" if C.module_of(cfg, comp) else "standalone"
                out[place] |= {
                    k for k in (cfg.get(comp) or {}) if not k.startswith("_")
                }
    return out


def test_every_object_key_is_exercised_in_both_placements():
    """gh-1651: a standalone object and a module object render through
    different paths, so a key covered in one placement proves nothing about
    the other. `class_name` was covered on a module object only
    (`_renamed_class`), and on a standalone object `jm apply` dropped it.

    Registration-free over keys: whatever the shapes set is what is checked.
    """
    by = _keys_by_placement()
    missing = sorted(
        f"{k!r}: {place} only"
        for place, other in (
            ("standalone", "module"),
            ("module", "standalone"),
        )
        for k in by[place] - by[other] - set(ONE_PLACEMENT)
    )
    assert not missing, (
        "these object keys are exercised in one placement only -- add a "
        "shape for the other, or list the key in ONE_PLACEMENT with why:\n  "
        + "\n  ".join(missing)
    )
    stale = sorted(
        k for k in ONE_PLACEMENT if k in by["standalone"] and k in by["module"]
    )
    assert not stale, f"ONE_PLACEMENT lists keys covered in both: {stale}"


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
#
# The script is run by `_jmrun.script_round_trip`, the one replayer every
# such test shares (gh-1931): this file kept a third copy, with its own
# grammar and its own refusals, after gh-1923 had folded the other two in.


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=sorted(SHAPES))
def test_script_round_trips_the_manifest(tmp_path, shape):
    root = SHAPES[shape](tmp_path)
    before, after = script_round_trip(root, tmp_path / "replay")
    # The layout is not this gate's question, and a script cannot yet keep
    # it: SHAPES build the single-manifest layout, which `jm script` replays
    # as fragments (gh-1918). What is compared is the merged manifest, as it
    # always was here; drop this when gh-1918 lands.
    for side in (before, after):
        side.pop("include")
    assert after == before, (
        f"`jm script` does not round-trip a {shape} project. The emitted "
        "script rebuilds a DIFFERENT manifest, which is worse than failing "
        "loudly — usually a key `jm method`/`jm object` accepts that "
        "`_script.py` never learned to emit.\n"
        f"emitted script:\n{run_cli('script', cwd=root).stdout}"
    )


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=sorted(SHAPES))
def test_the_replayed_project_is_clean(tmp_path, shape):
    """gh-2215: the tree a script rebuilds is in sync, not just its manifest.

    A manifest can round-trip while the tree under it does not: an `object`
    reference resolved by a command that ran too early writes a `_core.c`
    that no later command revisits. So the replay must report nothing under
    `status --check`, and `apply` on it must change nothing.
    """
    root = SHAPES[shape](tmp_path)
    script_round_trip(root, tmp_path / "replay")
    replayed = tmp_path / "replay" / "demo"
    s = run_cli("status", "--check", cwd=replayed)
    assert s.returncode == 0, s.stdout + s.stderr
    before = _tree_digest(replayed)
    a = run_cli("apply", cwd=replayed)
    assert a.returncode == 0, a.stdout + a.stderr
    after = _tree_digest(replayed)
    moved = sorted(k for k in before if before[k] != after.get(k))
    assert not moved, f"`jm apply` rewrote {moved} on the replayed {shape}"


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
    h = root / INC_ROOT / comp / f"{comp}_core.h"
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


# ── every writer parameter, named at the replay ─────────────────────────────
# gh-2215: `_replay_property` forwarded every key but `capsule_type`, and every
# arm above passed, because none of them is told which keys exist: the shapes
# only cover the keys someone thought to put in a shape, and the per-kind key
# lists (`test_gh663_apply_threads_method_keys`) cover one kind and are lists.
# The writers' signatures are the one place every key is already declared. So
# this arm reads each `<writer>.run(...)` call in `_apply.py` and requires it
# to bind EVERY parameter its `run` takes -- positionally, by keyword, or
# through a `**helper(...)` whose body returns a dict literal. A parameter
# the replay does not need is still named, with the reason beside it; an
# omission and a decision then look different, which is the whole point.


def _apply_tree() -> "ast.Module":
    from just_makeit import _apply

    return ast.parse(Path(_apply.__file__).read_text(encoding="utf-8"))


def _dict_helper_keys(tree: "ast.Module", name: str) -> "set[str]":
    """The keys of the dict literal module-level function *name* returns."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(
                    ret.value, ast.Dict
                ):
                    keys = ret.value.keys
                    assert all(isinstance(k, ast.Constant) for k in keys), (
                        f"{name}() returns a dict this gate cannot read"
                    )
                    return {k.value for k in keys}  # type: ignore[union-attr]
    raise AssertionError(
        f"`**{name}(...)` is splatted into a writer, and {name} is not a "
        "module-level function returning a dict literal -- this gate cannot "
        "see which parameters it binds"
    )


def _writer_calls() -> "list[tuple[int, str, set[str]]]":
    """``(line, writer, unbound)`` for every ``_<writer>.run(...)`` call."""
    import importlib
    import inspect

    tree = _apply_tree()
    out = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id.startswith("_")
        ):
            continue
        writer = node.func.value.id
        mod = importlib.import_module(f"just_makeit.{writer}")
        params = list(inspect.signature(mod.run).parameters)
        assert not any(isinstance(a, ast.Starred) for a in node.args), (
            f"_apply.py:{node.lineno}: `*args` into {writer}.run hides "
            "which parameters it binds"
        )
        bound = set(params[: len(node.args)])
        for kw in node.keywords:
            if kw.arg is not None:
                bound.add(kw.arg)
            else:
                call = kw.value
                assert isinstance(call, ast.Call) and isinstance(
                    call.func, ast.Name
                ), f"_apply.py:{node.lineno}: an unreadable `**` splat"
                bound |= _dict_helper_keys(tree, call.func.id)
        out.append((node.lineno, writer, set(params) - bound))
    return out


def test_the_replay_names_every_writer_parameter():
    calls = _writer_calls()
    # Armed: the replay's own writers are all among what was read. A scan
    # that found no call would pass for the wrong reason.
    found = {w for _line, w, _u in calls}
    for writer in ("_new", "_module", "_object", "_method", "_property"):
        assert writer in found, f"no {writer}.run call found in _apply.py"
    unbound = sorted(
        f"_apply.py:{line}: {writer}.run({', '.join(sorted(u))})"
        for line, writer, u in calls
        if u
    )
    assert not unbound, (
        "`jm apply` replays the manifest through each writer's `run`, and "
        "these calls leave parameters to their defaults -- a manifest key "
        "the writer accepts and the replay drops (gh-2215's `capsule_type`). "
        "Forward each from the manifest, or name it with the value the "
        "replay means and why:\n  " + "\n  ".join(unbound)
    )
