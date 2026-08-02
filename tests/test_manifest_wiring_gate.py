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
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
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


SHAPES = {
    "standalone": _standalone,
    "module": _module,
    "serializable": _serializable,
    "variable_output": _variable_output,
    "view": _view,
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


DOC_SURFACES = {
    "create_brief": (_shape_create, "SENTINEL0."),
    "reset_brief": (_shape_reset, "SENTINEL1"),
    "step_brief": (_shape_step, "SENTINEL2."),
    "accessor_brief": (_shape_accessor, "SENTINEL3."),
    "module_doc": (_shape_module_doc, "SENTINEL4."),
    "property_doc": (_shape_property_doc, "SENTINEL5."),
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
