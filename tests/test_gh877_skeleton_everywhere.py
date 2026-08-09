"""Every built-in falls back to the section skeleton, on both stub faces.

gh-877: for an object with no authored header Doxygen, the standalone `.pyi`
rendered `step` with `Parameters`/`Returns` and `steps` with a bare one-line
summary. Same file, same object, same "nothing authored" input, two answers —
and `steps` is where the types are least guessable (an `NDArray` in, an
`NDArray` out, plus an `out=` buffer), so it was the member that benefited most
from the skeleton and the one going without.

The whole suite passed before this file existed, which is the reason it does:
nothing pinned the fallback shape of `steps` on either face, so the
inconsistency could be introduced or removed without a single test noticing.
[[feedback_sabotage_check_tests]] — a change no test can see is a change no
test protects.

`reset` is the interesting case and is asserted too. It has no parameters and
returns `None`, so the renderer has no section it can fill and it still emits
its summary alone. That is not an exception to "skeleton everywhere" but the
rule applied to a member with nothing else to state — and the distinction
matters, because emitting a two-line spelling of the same sentence would report
drift in every existing project (`jm status --check` compares byte-for-byte) in
exchange for a newline.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _scaffold(root: Path, module: str | None) -> Path:
    """Scaffold one object with NO authored Doxygen; return its ``.pyi``.

    In-process rather than through the CLI, matching
    ``test_face_parity_gate._scaffold`` — same producers, and no dependency on
    a built entry point.
    """
    root.mkdir(parents=True, exist_ok=True)
    new_run("p", root, [], [])
    if module:
        module_run(root, module)
    object_run(root, "w", module, arg_type="float", return_type="float")
    return (
        root / "src" / "p" / module / f"{module}.pyi"
        if module
        else root / "src" / "p" / "w.pyi"
    )


def _docstring_of(pyi: Path, member: str) -> str:
    """The raw docstring body of ``def <member>`` in *pyi*."""
    text = pyi.read_text()
    marker = f"def {member}("
    i = text.index(marker)
    q = chr(34) * 3
    start = text.index(q, i) + len(q)
    return text[start : text.index(q, start)]


def test_steps_falls_back_to_the_section_skeleton(tmp_path):
    """Unauthored `steps` documents its parameter and return types."""
    for module in (None, "m"):
        pyi = _scaffold(tmp_path / (module or "sa_only"), module)
        doc = _docstring_of(pyi, "steps")
        face = module or "standalone"
        assert "Parameters" in doc, (
            f"[{face}] steps() has no Parameters section — the member whose "
            f"types are least guessable is the one documenting them least:\n"
            f"{doc}"
        )
        assert "Returns" in doc, f"[{face}] steps() has no Returns:\n{doc}"
        assert "NDArray[np.float32]" in doc, (
            f"[{face}] steps() names no array type, so the skeleton is "
            f"present but empty of the information it exists to carry:\n{doc}"
        )


def test_step_keeps_its_sections(tmp_path):
    """`step` documented sections before gh-877 and must still."""
    for module in (None, "m"):
        pyi = _scaffold(tmp_path / f"step_{module or 'sa'}", module)
        doc = _docstring_of(pyi, "step")
        face = module or "standalone"
        assert "Parameters" in doc and "Returns" in doc, (
            f"[{face}] step() lost its sections:\n{doc}"
        )


def test_reset_stays_a_one_line_summary(tmp_path):
    """A member with no sections to fill keeps the one-line form.

    The churn guard. `reset` takes nothing and returns `None`, so the skeleton
    has nothing to add; rendering it long would rewrite the docstring in every
    existing project to say exactly what it already said.
    """
    for module in (None, "m"):
        pyi = _scaffold(tmp_path / f"reset_{module or 'sa'}", module)
        doc = _docstring_of(pyi, "reset")
        face = module or "standalone"
        assert "\n" not in doc, (
            f"[{face}] reset() is no longer a one-line docstring. It has no "
            f"parameters and no return value, so the long form carries no "
            f"more information — and byte-for-byte `jm status --check` "
            f"reports that as drift in every downstream tree:\n{doc!r}"
        )
        assert doc.strip() == "Reset state to post-create defaults."
