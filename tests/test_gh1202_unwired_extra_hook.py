"""gh-1202: a hand-written hook nothing includes is reported, not swallowed.

A `*_extra.c` beside a generated `_ext.c` is the escape hatch for code that
cannot live in the manifest — a hand-written CPython type, a property
`value_fn` returning `PyObject *` (gh-543). jm documents it as a file it
"never creates or modifies"; it only `#include`s it.

`kind = "handle"` and `kind = "capsule"` emit an `_ext.c` that includes none,
so a file placed there by analogy with an object module compiles into nothing.
Measured before this was written: `jm apply` said nothing, `jm status` said
nothing, and no generated C or CMake referenced it.

**The gap is not the bug.** Those kinds simply lack a feature, and a project
without it still builds and passes — that is not what "green from day one"
forbids. What it forbids is jm letting someone do a reasonable thing and
quietly dropping it. So this reports; it does not build the hook.

Two design points the tests below pin:

* **The detector asks the artifact, not a table of kinds.** For each generated
  `_ext.c` it checks whether that file includes the hooks beside it. A list of
  which kinds support a hook would go stale the first time a kind is added —
  and the same check then covers a *second* route to the same symptom, where a
  kind that does support the hook has not re-rendered since the file appeared.
* **It is advisory, never gating.** For a handle or capsule there is nothing
  the reader can do, and a gate whose finding cannot be cleared teaches people
  to ignore the gate. That lesson is expensive enough to have its own history
  here; this does not repeat it.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _report  # noqa: E402
from just_makeit._extrahook import (  # noqa: E402
    HOOK_SUFFIXES,
    _SEEN,
    describe,
    unwired_hooks,
    warn_unwired_hooks,
)


def _tree(tmp_path: Path, ext_c_body: str, hooks: dict[str, str]) -> Path:
    """A minimal `native/src/<name>/` holding a generated `_ext.c` + hooks."""
    d = tmp_path / "native" / "src" / "m"
    d.mkdir(parents=True)
    (d / "m_ext.c").write_text(ext_c_body, encoding="utf-8")
    for name, body in hooks.items():
        (d / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_a_hook_the_ext_c_does_not_include_is_found(tmp_path: Path):
    root = _tree(
        tmp_path,
        "#include <Python.h>\n",
        {"m_ext_extra.c": "/* hand-written */\n"},
    )
    assert unwired_hooks(root) == [
        (Path("native/src/m/m_ext_extra.c"), Path("native/src/m/m_ext.c"))
    ]


def test_a_hook_the_ext_c_includes_is_not_reported(tmp_path: Path):
    """The false-positive direction, and the one that decides whether this is
    usable: an object module wires its hooks, and must stay silent."""
    root = _tree(
        tmp_path,
        '#include <Python.h>\n#include "m_ext_extra.c"  /* hand-written */\n',
        {"m_ext_extra.c": "/* hand-written */\n"},
    )
    assert unwired_hooks(root) == []


def test_a_mention_in_prose_does_not_count_as_wiring(tmp_path: Path):
    """Generated files are full of commentary about these hooks, so the match
    is anchored to a real `#include` at the start of a line. An unanchored
    substring search would read jm's own explanation of the feature as proof
    the feature happened."""
    root = _tree(
        tmp_path,
        "#include <Python.h>\n"
        "/* A hand-written m_ext_extra.c is included here when present. */\n",
        {"m_ext_extra.c": "/* hand-written */\n"},
    )
    assert len(unwired_hooks(root)) == 1


def test_both_hook_suffixes_are_watched(tmp_path: Path):
    root = _tree(
        tmp_path,
        "#include <Python.h>\n",
        {
            "m_ext_extra.c": "/* a */\n",
            "m_ext_prologue.c": "/* b */\n",
            "m_ext_o1_extra.c": "/* c */\n",
        },
    )
    names = sorted(h.name for h, _ in unwired_hooks(root))
    assert names == [
        "m_ext_extra.c",
        "m_ext_o1_extra.c",
        "m_ext_prologue.c",
    ]
    assert all(n.endswith(HOOK_SUFFIXES) for n in names)


def test_a_directory_with_no_generated_ext_c_is_left_alone(tmp_path: Path):
    """jm did not write that layout, so it has no basis for an opinion — and
    a project keeping unrelated C under `native/src/` must not be nagged."""
    d = tmp_path / "native" / "src" / "vendor"
    d.mkdir(parents=True)
    (d / "vendor_extra.c").write_text("/* not jm's */\n", encoding="utf-8")
    assert unwired_hooks(tmp_path) == []


def test_a_tree_with_no_native_src_is_not_an_error(tmp_path: Path):
    assert unwired_hooks(tmp_path) == []


def test_the_advice_never_sends_the_reader_to_regenerate():
    """`jm regenerate` re-renders the file and would wire the hook in -- and
    deletes the hook while doing so (gh-1216). The obvious remedy is the
    destructive one, so the message must warn about it rather than suggest it.

    Pinned as a test because this is exactly the sentence a later edit would
    "helpfully" add.
    """
    msg = describe(
        Path("native/src/m/m_ext_extra.c"), Path("native/src/m/m_ext.c")
    )
    assert "gh-1216" in msg
    assert "Do NOT reach for `jm regenerate`" in msg
    # ...and it still says what DOES work, or it is only half a report.
    assert "jm apply" in msg
    assert "gh-1202" in msg


def test_the_warning_is_advisory_and_does_not_gate(tmp_path: Path):
    """A handle or capsule has no hook to wire, so the reader cannot clear
    this finding. Gating on it would turn a downstream permanently red over a
    feature jm has not built."""
    root = _tree(
        tmp_path, "#include <Python.h>\n", {"m_ext_extra.c": "/* x */\n"}
    )
    _SEEN.clear()
    _report.reset()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        emitted = warn_unwired_hooks(root, stream=buf)
    assert len(emitted) == 1
    assert _report.gating_count() == 0, "must not count as drift"
    assert "warning" in buf.getvalue()


def test_the_same_hook_is_reported_once_per_process(tmp_path: Path):
    """`apply` loads the real tree and its temp scaffold; a reader should hear
    each thing once."""
    root = _tree(
        tmp_path, "#include <Python.h>\n", {"m_ext_extra.c": "/* x */\n"}
    )
    _SEEN.clear()
    buf = io.StringIO()
    first = warn_unwired_hooks(root, stream=buf)
    second = warn_unwired_hooks(root, stream=buf)
    assert len(first) == 1 and second == []
