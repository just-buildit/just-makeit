"""gh-996: a method entry may not claim a name the class already uses.

gh-994's sibling, and its complement. That issue was about the names where a
declared method and something jm generates are plausibly the *same* member —
an entry naming `reset` describes the built-in, an entry naming `reset(start)`
replaces it, and either way jm emits exactly one. This file is the set where
no such reading exists, and where jm accepted the entry anyway and bound
something other than what the manifest asked for.

Measured on 0.60.2, one entry per name, on a fresh object:

| entry            | what jm did                                            |
| ---------------- | ------------------------------------------------------ |
| `create`         | **0** PyMethodDef rows, **0** `.pyi` entries — bound    |
|                  | nothing at all, since the constructor's Python face is  |
|                  | `__init__` and there is no `create()` to absorb into    |
| `__enter__`      | **2** rows; the second shadows jm's, so `with obj:`     |
| `__exit__`       | stopped returning the object / tearing it down          |
| `stream`         | **2** rows on a `--streamable` object, shadowing the    |
|                  | gh-201 generator                                        |
| `state_bytes`    | **2** rows and an `_ext.c` that does not compile —      |
| `get_state`      | `redefinition of Osc_state_bytes` and friends (gh-400)  |
| `set_state`      |                                                        |
| renamed teardown | **2** rows for `[<obj>.destroy] name = "close"`         |
| a property       | **3** `.pyi` entries for one name                       |

Every row is a mistake with exactly one honest answer, so every row is
refused. Absorbing them instead — the gh-994 treatment — would keep the
silence this issue is about: the entry would still not produce what it asked
for, jm would just stop tripping over it.

The refusal happens before the command writes anything, which is the gh-910
rule one command over: a name jm will not accept must not leave a half-made
tree behind for someone to clean up.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _cli_object, _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")


def _plain(root: Path, monkeypatch) -> Path:
    """An object with a scalar field, so the accessor pair exists."""
    new_run("proj", root)
    monkeypatch.chdir(root)
    _cli_object.run(["osc", "--state", "gain:double:1.0"])
    return root


def _streamable(root: Path, monkeypatch) -> Path:
    new_run("proj", root)
    monkeypatch.chdir(root)
    _cli_object.run(
        ["osc", "--arg-type", "void", "--return-type", "float", "--streamable"]
    )
    return root


def _serializable(root: Path, monkeypatch) -> Path:
    new_run("proj", root)
    monkeypatch.chdir(root)
    _cli_object.run(["osc", "--state", "gain:double:1.0", "--serializable"])
    return root


def _add(root: Path, name: str) -> None:
    method_run(root, "osc", name, None, "void", "double", False, [])


class TestReservedNamesAreRefused:
    @pytest.mark.parametrize(
        "scaffold,name",
        [
            (_plain, "create"),
            (_plain, "__enter__"),
            (_plain, "__exit__"),
            (_streamable, "stream"),
            (_streamable, "__iter__"),
            (_serializable, "state_bytes"),
            (_serializable, "get_state"),
            (_serializable, "set_state"),
        ],
    )
    def test_generated_member_name_is_refused(
        self, tmp_path, monkeypatch, capsys, scaffold, name
    ):
        root = scaffold(tmp_path / "proj", monkeypatch)
        with pytest.raises(SystemExit) as exc:
            _add(root, name)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert name in err
        # The message has to say what holds the name and what to do — a bare
        # refusal on a name the author cannot see in their own manifest is
        # the papercut, not the fix.
        assert "already provided by" in err
        # ...and a remedy that WORKS. The obvious-looking one does not:
        # --fn renames the C SYMBOL, while every collision here is on the
        # PYTHON name, so the PyMethodDef table still ends up with two rows
        # under one key. Measured at two rows, after this message shipped
        # advising it. Pinned in both directions so the wrong advice cannot
        # come back.
        assert "--fn" not in err
        assert "rename the method" in err

    def test_a_declared_property_name_is_refused(
        self, tmp_path, monkeypatch, capsys
    ):
        root = _plain(tmp_path / "proj", monkeypatch)
        property_run(root, "osc", "level", None, "double", True)
        with pytest.raises(SystemExit):
            _add(root, "level")
        assert "a declared property" in capsys.readouterr().err

    def test_a_renamed_teardowns_name_is_refused(self, tmp_path, capsys):
        """`[<obj>.destroy] name = "close"` moves the member, and the guard.

        The reserved set is derived, so renaming the teardown renames what is
        reserved: `close` becomes unavailable and `destroy` — no longer a
        member at all — is free. A hardcoded word list gets this exactly
        backwards.
        """
        root = tmp_path / "proj"
        frag = tmp_path / "frag.toml"
        frag.write_text(
            '[osc]\narg_type = "void"\nreturn_type = "double"\n'
            'mutable = "true"\n\n'
            '[osc.destroy]\nname = "close"\n\n'
            '[[osc.state]]\nname = "gain"\ntype = "double"\n'
            'default = "1.0"\n\n'
            '[[osc.methods]]\nname = "close"\narg_type = "void"\n'
            'return_type = "double"\n',
            encoding="utf-8",
        )
        new_run("proj", root)
        with pytest.raises(SystemExit):
            apply_run(root, fragment=frag)
        assert "the teardown binding" in capsys.readouterr().err

    def test_nothing_is_written_before_the_refusal(
        self, tmp_path, monkeypatch
    ):
        """gh-910's rule: a refused command leaves no half-made tree."""
        root = _plain(tmp_path / "proj", monkeypatch)
        before = {
            p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
        }
        with pytest.raises(SystemExit):
            _add(root, "create")
        after = {
            p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
        }
        assert after == before


class TestAbsorbableNamesStayAccepted:
    """The six gh-994 settles must not be swept up by the refusal.

    doppler declares `reset` in 28 objects and jm's own fixtures declare it
    all over, because gh-131 made it a supported pattern. A guard that
    reserved every name jm generates would refuse all of them — which is the
    version of this that was tried, and put 85 of jm's own tests red.
    """

    @pytest.mark.parametrize(
        "name",
        ["reset", "destroy", "step", "steps", "get_gain", "set_gain"],
    )
    def test_still_accepted(self, tmp_path, monkeypatch, name):
        root = _plain(tmp_path / "proj", monkeypatch)
        _add(root, name)
        declared = [m["name"] for m in C.methods(C.load(root), "osc")]
        assert name in declared

    def test_destroy_is_free_once_the_teardown_is_renamed(self, tmp_path):
        """The mirror of the renamed-teardown refusal.

        With `name = "close"` there is no `destroy()` member, so an entry
        named `destroy` collides with nothing — it is an ordinary method.
        Asserting only the refusal would leave a guard that could be
        over-broad in this direction without anything noticing.
        """
        root = tmp_path / "proj"
        frag = tmp_path / "frag.toml"
        frag.write_text(
            '[osc]\narg_type = "void"\nreturn_type = "double"\n'
            'mutable = "true"\nno_step = "true"\n\n'
            '[osc.destroy]\nname = "close"\n\n'
            '[[osc.methods]]\nname = "destroy"\narg_type = "void"\n'
            'return_type = "double"\n',
            encoding="utf-8",
        )
        new_run("proj", root)
        apply_run(root, fragment=frag)
        ext = (root / "native/src/osc/osc_ext.c").read_text(encoding="utf-8")
        assert len(re.findall(r'\{"destroy",', ext)) == 1
        assert len(re.findall(r'\{"close",', ext)) == 1


@_needs_cc
class TestTheAcceptedTreeStillCompiles:
    """A guard that refuses everything would pass every test above.

    So the accepted side is compiled, not just counted: whatever survives the
    reserved-name check has to still produce a translation unit.
    """

    @pytest.mark.parametrize(
        "name",
        ["reset", "destroy", "step", "steps", "get_gain", "set_gain"],
    )
    def test_ext_c_compiles(self, tmp_path, monkeypatch, name):
        numpy = pytest.importorskip("numpy")
        root = _plain(tmp_path / "proj", monkeypatch)
        _add(root, name)
        # monkeypatch.chdir left us inside the project; compile by abspath.
        proc = subprocess.run(
            [
                _CC,
                "-fsyntax-only",
                "-std=gnu99",
                f"-I{root / 'native' / 'inc'}",
                f"-I{sysconfig.get_paths()['include']}",
                f"-I{numpy.get_include()}",
                str(root / "native" / "src" / "osc" / "osc_ext.c"),
            ],
            capture_output=True,
            text=True,
            cwd=os.fspath(root),
        )
        assert proc.returncode == 0, proc.stderr
