"""gh-1867: a `jm regenerate` that fails leaves the component it found.

`regenerate` deleted every file the component owned and only then ran
`apply` to write them back, so any failure in that `apply` lost the
component -- its hand-written ``_core.c`` included. Two triggers were
measured on v0.98.1:

- an ``impl_file`` naming a file inside the component, which the delete
  had just removed: `apply` refused "--impl file not found" about it;
- any refusal of the re-apply, even one about ANOTHER component's row --
  the issue's was gh-1885's poisoned row, here a string step type
  (gh-1884), which `apply` refuses before it writes.

Both now leave the tree byte-identical. The first is refused before
anything is deleted (`_regenerate.deleted_impl_sources`), so its message is
true. The second is put back by the record every mutating command keeps
(`_undo`, gh-1867/gh-2040), which also covers `jm add --state`: it saves the
manifest and rebuilds through the same path.

GATE: each trigger exits 1 with one ``error:`` line naming its cause, and
      leaves every file and directory as it was, the author's edit to
      ``_core.c`` included. The first writes nothing at all (no "put back");
      the others are proven to have written before they failed.

`tests/test_gh2057_verb_leaves_what_apply_writes.py` holds every mutating
verb to the same with an injected failure; `test_gh1884_str_step_type_
refused.py` holds `jm method` and `jm property` to it on a real one
(gh-2040).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli

#: Appended to the component's ``_core.c``: the author's edit the deleted
#: component took with it.
MINE = b"/* the author's own */\n"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """A project with one object `o`, built by running jm."""
    base = tmp_path_factory.mktemp("gh1867")
    _ok("new", "p", "--object", "o", cwd=base)
    return base / "p"


@pytest.fixture
def root(built: Path, tmp_path: Path) -> Path:
    """A copy of *built* with the author's edit in ``o_core.c``."""
    dst = tmp_path / "p"
    shutil.copytree(built, dst)
    core_c = dst / "native/src/o/o_core.c"
    core_c.write_bytes(core_c.read_bytes() + MINE)
    return dst


def _tree(root: Path) -> "dict[str, bytes]":
    """Every file under *root*, and every directory as an empty marker."""
    return {
        p.relative_to(root).as_posix(): (
            p.read_bytes() if p.is_file() else b"<dir>"
        )
        for p in sorted(root.rglob("*"))
        if "__pycache__" not in p.parts
    }


def _fails_and_leaves_it(root: Path, args, says: str):
    """*args* exits 1 with one ``error:`` line saying *says*, and the tree
    is as it was. Returns the run."""
    before = _tree(root)
    r = run_cli(*args, cwd=root)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode == 1 and len(errors) == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert says in r.stderr, r.stderr
    after = _tree(root)
    changed = sorted(k for k in before.keys() | after.keys()
                     if before.get(k) != after.get(k))  # fmt: skip
    assert changed == [], changed
    return r


def test_an_impl_file_inside_the_component_is_refused_first(root):
    """The issue's repro: refused before the delete, so nothing is written
    and the message names the key, not a missing file that is there."""
    frag = root / "objects/o.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.startswith("[o]\n"), text
    frag.write_text(
        text.replace(
            "[o]\n",
            '[o]\nimpl_file = "native/src/o/o_core.c::p_o_create"\n',
            1,
        ),
        encoding="utf-8",
    )

    r = _fails_and_leaves_it(
        root, ("regenerate", "o", "--force"), says="o.impl_file = "
    )
    assert "not found" not in r.stderr, r.stderr
    assert "put back" not in r.stdout, "it wrote before it refused"


#: Another component's row the re-apply refuses: a string step type, which
#: `apply` refuses before it writes (gh-1884).
POISON = '[pk]\narg_type = "const char *"\nreturn_type = "float"\n'


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("regenerate", "o", "--force"), id="regenerate"),
        pytest.param(
            ("regenerate", "o", "--force", "--discard"), id="discard"
        ),
        pytest.param(
            ("add", "--object", "o", "--state", "y:double:0", "--force"),
            id="add-state",
        ),
    ],
)
def test_a_refused_re_apply_puts_the_component_back(root, args):
    """The comment's repro: the refusal is about `pk`, and `o` -- deleted
    for the rebuild -- comes back with the author's edit in it."""
    (root / "objects/pk.toml").write_text(POISON, encoding="utf-8")

    r = _fails_and_leaves_it(root, args, says="'pk' arg_type")
    # It had deleted the component before `apply` refused.
    assert "put back the" in r.stdout, r.stdout
    assert (root / "native/src/o/o_core.c").read_bytes().endswith(MINE)
