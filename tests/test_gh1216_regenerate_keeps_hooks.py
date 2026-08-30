"""gh-1216: `jm regenerate` must not delete a hand-written hook.

`*_extra.c` and `*_prologue.c` are the files jm documents as "jm never creates
or modifies" — the escape hatch for a hand-written CPython type or a property
`value_fn` returning `PyObject *` (gh-543), i.e. precisely the code that cannot
be reproduced from the manifest.

`regenerate` removed the component directory and rebuilt it, so it destroyed
them — the one operation they exist to survive. Nothing named them: no prompt,
no line in the output. Its existing warning covers `_core.c` / `_core.h` and
promises those are "lifted and spliced back in afterward", which a `*_extra.c`
is not and does not get.

Found while writing gh-1202's detector: the natural advice for an unwired hook
is "re-render that file", and the command that does that ate it. The warning
then disappeared — because the file was gone — which reads as success.

The fix is in `_rm`, not in `regenerate`: jm does not own those files, so it
has no basis for deleting them anywhere. That covers `remove` too, where the
directory is left behind holding only the author's file. An orphan is
recoverable; a deletion is not.

**The contents are asserted, not just the path.** A hook recreated empty would
satisfy an existence check while having lost everything that mattered.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._extrahook import unwired_hooks  # noqa: E402
from just_makeit._init import run as init_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._regenerate import run as regenerate_run  # noqa: E402
from just_makeit._remove import _rm  # noqa: E402

MARKER = "static int solo_marker = 42;"
HOOK_BODY = f"/* hand-written -- MUST SURVIVE */\n{MARKER}\n"


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run(
            "p",
            proj,
            object_names=[],
            state_vars=[],
            arg_type="float",
            return_type="float",
            pytest_=False,
            pytest_benchmark_=False,
        )
        init_run(proj, "solo", state_vars=[("g", "float", "1.0f")])
    return proj


def test_regenerate_preserves_the_hook_and_its_contents(tmp_path: Path):
    proj = _project(tmp_path)
    hook = proj / "native" / "src" / "solo" / "solo_ext_extra.c"
    hook.write_text(HOOK_BODY, encoding="utf-8")

    with contextlib.redirect_stdout(io.StringIO()):
        regenerate_run(proj, "solo", force=True)

    assert hook.exists(), "regenerate deleted the hand-written hook"
    assert MARKER in hook.read_text(encoding="utf-8"), (
        "hook survived as a path but lost its contents"
    )


def test_regenerate_wires_the_surviving_hook_back_in(tmp_path: Path):
    """Preserving it is half the fix.

    A hook that survives but is not `#include`d is present, intact, and still
    compiling into nothing — the same silence gh-1202 reports. Before this,
    there was no way at all to get a standalone object's hook wired: the
    component must exist before a file can sit beside it, and `apply` reads the
    manifest, which the file is not in.
    """
    proj = _project(tmp_path)
    hook = proj / "native" / "src" / "solo" / "solo_ext_extra.c"
    hook.write_text(HOOK_BODY, encoding="utf-8")

    with contextlib.redirect_stdout(io.StringIO()):
        regenerate_run(proj, "solo", force=True)

    ext_c = (proj / "native" / "src" / "solo" / "solo_ext.c").read_text(
        encoding="utf-8"
    )
    assert '#include "solo_ext_extra.c"' in ext_c
    # ...and the gh-1202 detector agrees, so the two cannot drift apart.
    assert unwired_hooks(proj) == []


def test_rm_keeps_a_hook_and_deletes_everything_else(tmp_path: Path):
    """The fix is in `_rm`, so it holds for every caller — `remove` included."""
    d = tmp_path / "native" / "src" / "m"
    d.mkdir(parents=True)
    (d / "m_ext.c").write_text("generated\n", encoding="utf-8")
    (d / "m_core.c").write_text("generated\n", encoding="utf-8")
    (d / "m_ext_extra.c").write_text(HOOK_BODY, encoding="utf-8")
    (d / "m_ext_prologue.c").write_text("/* p */\n", encoding="utf-8")

    with contextlib.redirect_stdout(io.StringIO()):
        _rm(d)

    assert d.is_dir(), "a directory holding a hook must be kept"
    assert sorted(p.name for p in d.iterdir()) == [
        "m_ext_extra.c",
        "m_ext_prologue.c",
    ]
    assert MARKER in (d / "m_ext_extra.c").read_text(encoding="utf-8")


def test_rm_still_removes_a_directory_with_no_hook(tmp_path: Path):
    """The preservation must not turn `_rm` into a no-op — `remove` and
    `regenerate` depend on the tree actually going away."""
    d = tmp_path / "native" / "src" / "m"
    d.mkdir(parents=True)
    (d / "m_ext.c").write_text("generated\n", encoding="utf-8")
    (d / "m_core.c").write_text("generated\n", encoding="utf-8")

    with contextlib.redirect_stdout(io.StringIO()):
        _rm(d)

    assert not d.exists()


def test_rm_keeps_a_hook_named_directly(tmp_path: Path):
    """Not only as a directory member: `_rm` is also called on single files."""
    d = tmp_path / "x"
    d.mkdir()
    hook = d / "m_ext_extra.c"
    hook.write_text(HOOK_BODY, encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        _rm(hook)
    assert hook.exists()


def test_rm_says_what_it_kept(tmp_path: Path):
    """Silence is what made the deletion expensive; the reverse must be
    audible, or a reader cannot tell a preserved file from one jm rewrote."""
    d = tmp_path / "native" / "src" / "m"
    d.mkdir(parents=True)
    (d / "m_ext.c").write_text("generated\n", encoding="utf-8")
    (d / "m_ext_extra.c").write_text(HOOK_BODY, encoding="utf-8")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _rm(d)
    out = buf.getvalue()
    assert "keep" in out and "m_ext_extra.c" in out
    assert "hand-written" in out
