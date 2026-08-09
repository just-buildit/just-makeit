"""A module can include hand-written C *before* its per-object fragments.

gh-862: every hand-written hook in the aggregator landed after the code that
would use it — `<module>_ext_<obj>_extra.c` after its own fragment, and
`<module>_ext_extra.c` after all of them. So when two objects in one module
needed the same helper, there was nowhere to put it that both fragments could
call. A declaration included after its callers is not available to them.

doppler's `telemetry` module is the worked example: `read_dict()` on two
objects shares ~55 lines that cannot be generated (the return is a dict whose
keys come from a runtime registry and whose values are arrays of
data-dependent length), and those lines were duplicated verbatim across two
*sacred* fragments — the shape that drifts, since a fix applied to one copy
leaves the other wrong.

`<module>_ext_prologue.c` is that hook. Like the other two it is discovered by
existence and never created or modified by jm; unlike them it is included
first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _module_project(root: Path, module: str = "m") -> tuple[Path, Path]:
    """Scaffold a two-object module; return (native ext dir, aggregator)."""
    root.mkdir(parents=True, exist_ok=True)
    new_run("p", root, [], [])
    module_run(root, module)
    for obj in ("a", "b"):
        object_run(root, obj, module, arg_type="float", return_type="float")
    cname = module.replace(".", "_")
    ext_dir = root / "native" / "src" / cname
    return ext_dir, ext_dir / f"{cname}_ext.c"


def test_absent_prologue_changes_nothing(tmp_path):
    """No file, no include — the hook costs nothing when unused."""
    _, agg = _module_project(tmp_path / "none")
    assert "_ext_prologue.c" not in agg.read_text()


def test_prologue_is_included_before_every_fragment(tmp_path):
    """The include lands ahead of the fragments, which is the entire point."""
    root = tmp_path / "with"
    ext_dir, agg = _module_project(root)
    (ext_dir / "m_ext_prologue.c").write_text(
        "/* hand-written */\nstatic int shared_helper(void) { return 1; }\n"
    )
    # Discovery is by existence at render time, so the aggregator has to be
    # rewritten after the file appears. Adding a third object does that, and
    # also proves the include stays ahead of a fragment added later.
    object_run(root, "c", "m", arg_type="float", return_type="float")
    text = agg.read_text()

    assert '#include "m_ext_prologue.c"' in text, (
        "the prologue exists but is not included:\n" + text
    )
    prologue_at = text.index('#include "m_ext_prologue.c"')
    fragment_ats = [
        text.index(f'#include "m_ext_{obj}.c"') for obj in ("a", "b", "c")
    ]
    assert all(prologue_at < at for at in fragment_ats), (
        "the prologue is included after a fragment, so a fragment cannot call "
        "into it — which is the whole reason this hook exists, and exactly "
        "what the two *_extra.c hooks already do."
    )


def test_prologue_is_marked_hand_written(tmp_path):
    """It carries the same never-modified marker as the other two hooks."""
    ext_dir, agg = _module_project(tmp_path / "marked")
    (ext_dir / "m_ext_prologue.c").write_text(
        "static int h(void){return 1;}\n"
    )
    object_run(
        tmp_path / "marked", "c", "m", arg_type="float", return_type="float"
    )
    line = next(
        ln for ln in agg.read_text().splitlines() if "m_ext_prologue.c" in ln
    )
    assert "hand-written" in line, (
        f"the include does not say jm never modifies it: {line!r}"
    )


def test_dotted_module_uses_the_flat_name(tmp_path):
    """A nested module id resolves the hook through `cname`, not the dots.

    `module_paths` splits the id's three roles, and the native directory and
    every file in it use `cname` (`dsp_filters`). Looking the hook up under
    the dotted spelling would silently never match — the failure would be a
    hook that appears to work and quietly does nothing for nested modules.
    """
    ext_dir, agg = _module_project(tmp_path / "dotted", module="dsp.filters")
    assert ext_dir.name == "dsp_filters"
    (ext_dir / "dsp_filters_ext_prologue.c").write_text(
        "static int h(void){return 1;}\n"
    )
    object_run(
        tmp_path / "dotted",
        "c",
        "dsp.filters",
        arg_type="float",
        return_type="float",
    )
    text = agg.read_text()
    assert '#include "dsp_filters_ext_prologue.c"' in text, (
        "the dotted module did not pick up its prologue:\n" + text
    )
    assert text.index('#include "dsp_filters_ext_prologue.c"') < text.index(
        '#include "dsp_filters_ext_a.c"'
    )
