"""gh-1273: an `[[enum]]` that gains a value must reach its C table.

**The defect.** A module object's binding lives in a *sacred* fragment,
`native/src/<mod>/<mod>_ext_<obj>.c`, which `jm apply` reconciles member by
member and never re-renders (gh-767). That rule protects *wrapper bodies*. It
was also freezing the `_enum_*` tables, which are not wrapper bodies: they are
a verbatim projection of `[[enum]]`, jm owns every byte, and there is nothing
in one to author.

So adding a third value to an existing enum moved everything jm re-renders --
the `.pyi` went to `Literal["none", "timecode", "sigmf"]` -- and left the
table at two entries. The getter is
`PyUnicode_FromString(_enum_Reader_t0_source[_v])`, so the new value read the
table's `NULL` terminator: not a wrong string, a crash, and only for the value
that was just added, which is exactly the case a smoke test does not reach.

**The half the issue did not name.** The refusal message carries the same
projection, so the setter *rejected* the value the stub had just started
advertising, with `(choices: none, timecode)`. Fixing only the table would
have left an object whose getter worked and whose setter refused.

**Why the standalone face was fine.** `native/src/<comp>/<comp>_ext.c` is
re-rendered whole, so its table and its message have always been correct. The
two faces disagreed for as long as they did because nothing compared them --
`test_every_enum_table_matches_the_manifest` is that comparison, and it asks
the manifest rather than asking one face about the other, so it is registration
-free over faces: a third face that freezes a table fails here on the day it
exists.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

sys.path.insert(0, str(SRC))

from just_makeit import _docsync  # noqa: E402

from _jmrun import JmRun, run_cli

#: `static const char *const _enum_Reader_t0[] = { ... };`
_TABLE_RE = re.compile(
    r"^static\s+const\s+char\s*\*\s*const\s+(_enum_\w+)\s*\[\s*\]\s*=\s*\{"
    r"(.*?)\}\s*;",
    re.S | re.M,
)
_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


def _enum_tables(root: Path) -> dict:
    """``{(relpath, symbol): [values]}`` for every enum table under native/.

    Derived from the tree with no list of files and no list of faces. A table
    jm learns to emit somewhere new is covered the day it is emitted.
    """
    out = {}
    for path in sorted((root / "native").rglob("*.c")):
        text = path.read_text(encoding="utf-8")
        for m in _TABLE_RE.finditer(text):
            key = (path.relative_to(root).as_posix(), m.group(1))
            out[key] = _STR_RE.findall(m.group(2))
    return out


def _resolve(symbol: str, enums: dict) -> str:
    """Which ``[[enum]]`` *symbol* is the table for.

    The symbol is `_enum_<Type>_<name>` in an object namespace and
    `_enum_<name>` bare (see `_enumc.symbols`), so the enum is whichever
    declared name the symbol ends with -- longest first, since one name may be
    a suffix of another.
    """
    for name in sorted(enums, key=len, reverse=True):
        if symbol == f"_enum_{name}" or symbol.endswith(f"_{name}"):
            return name
    return ""


_ENUM_TOML = """
[[enum]]
name = "t0"
values = ["none", "timecode"]
"""


@pytest.fixture
def project(tmp_path) -> Path:
    """The same enum on both faces: a module object and a standalone one.

    Both, because the bug is a disagreement between them and a fixture
    holding either one alone cannot see it -- the standalone face was correct
    throughout, so a sweep over only that face reports green on the broken
    release.
    """
    assert _cli("new", "rdr", cwd=tmp_path).returncode == 0
    root = tmp_path / "rdr"
    (root / "just-makeit.toml").write_text(
        (root / "just-makeit.toml").read_text(encoding="utf-8") + _ENUM_TOML,
        encoding="utf-8",
    )
    for args in (
        ("module", "wfm"),
        ("object", "reader", "--module", "wfm", "--state", "src:int:0"),
        ("object", "solo", "--state", "src:int:0"),
        (
            "property",
            "reader",
            "t0",
            "--module",
            "wfm",
            "--type",
            "int",
            "--enum",
            "t0",
            "--writable",
        ),
        (
            "property",
            "solo",
            "t0",
            "--type",
            "int",
            "--enum",
            "t0",
            "--writable",
        ),
    ):
        r = _cli(*args, cwd=root)
        assert r.returncode == 0, f"{args}\n{r.stdout}{r.stderr}"
    return root


def _add_a_value(root: Path) -> None:
    man = root / "just-makeit.toml"
    text = man.read_text(encoding="utf-8")
    assert 'values = ["none", "timecode"]' in text
    man.write_text(
        text.replace(
            'values = ["none", "timecode"]',
            'values = ["none", "timecode", "sigmf"]',
        ),
        encoding="utf-8",
    )


FRAG = "native/src/wfm/wfm_ext_reader.c"
SOLO = "native/src/solo/solo_ext.c"


class TestTheFixtureItself:
    def test_both_faces_declare_the_table(self, project: Path):
        """A sweep that found no table would pass for the wrong reason."""
        tables = _enum_tables(project)
        assert any(rel == FRAG for rel, _ in tables), sorted(tables)
        assert any(rel == SOLO for rel, _ in tables), sorted(tables)


class TestApplyReconcilesTheTable:
    def test_the_new_value_reaches_the_table(self, project: Path):
        _add_a_value(project)
        r = _cli("apply", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        frag = (project / FRAG).read_text(encoding="utf-8")
        assert '"sigmf",' in frag
        assert "NULL,\n};" in frag

    def test_the_refusal_message_moves_with_it(self, project: Path):
        """A setter refusing the value the stub advertises is the same bug."""
        _add_a_value(project)
        assert _cli("apply", cwd=project).returncode == 0
        frag = (project / FRAG).read_text(encoding="utf-8")
        assert "(choices: none, timecode, sigmf)" in frag
        assert "(choices: none, timecode)" not in frag

    def test_apply_says_what_it_changed(self, project: Path):
        _add_a_value(project)
        out = _cli("apply", cwd=project).stdout
        assert "enum table _enum_Reader_t0 (+sigmf)" in out
        assert "refreshed enum choices in the refusal for t0" in out

    def test_a_second_apply_changes_nothing(self, project: Path):
        """Idempotent, or every apply reports a diff it did not make."""
        _add_a_value(project)
        assert _cli("apply", cwd=project).returncode == 0
        before = (project / FRAG).read_text(encoding="utf-8")
        out = _cli("apply", cwd=project).stdout
        assert (project / FRAG).read_text(encoding="utf-8") == before
        assert "enum table" not in out


class TestEveryEnumTableMatchesTheManifest:
    """The property, asked of the manifest rather than of one face.

    Registration-free in both directions: over files, over faces, and over
    enums. Fail-closed on a table that resolves to no declared enum, so a
    symbol jm starts spelling differently shows up as a failure rather than
    quietly leaving the sweep with nothing to check.
    """

    def test_after_apply(self, project: Path):
        _add_a_value(project)
        assert _cli("apply", cwd=project).returncode == 0
        enums = {"t0": ["none", "timecode", "sigmf"]}
        tables = _enum_tables(project)
        assert tables, "no enum tables found — the sweep is not armed"
        wrong = []
        for (rel, sym), values in sorted(tables.items()):
            name = _resolve(sym, enums)
            assert name, f"{rel}: {sym} matches no [[enum]]"
            if values != enums[name]:
                wrong.append(
                    f"{rel}: {sym} = {values}, [[enum]] {name} = {enums[name]}"
                )
        assert not wrong, "\n".join(wrong)

    def test_the_two_faces_agree(self, project: Path):
        """The comparison that was never made.

        The standalone `_ext.c` is re-rendered whole and was always right;
        the sacred fragment was frozen. Stated as its own test because it is
        the shape of the bug, not just an instance of it.
        """
        _add_a_value(project)
        assert _cli("apply", cwd=project).returncode == 0
        tables = _enum_tables(project)
        frag = [v for (rel, _), v in tables.items() if rel == FRAG]
        solo = [v for (rel, _), v in tables.items() if rel == SOLO]
        assert frag and solo
        assert frag == solo


class TestTheSacredRuleStillHolds:
    def test_a_hand_written_wrapper_body_survives(self, project: Path):
        """Reconciling a table must not become a licence to re-render bodies."""
        frag = project / FRAG
        text = frag.read_text(encoding="utf-8")
        marker = "/* hand-written: do not touch */"
        assert "Reader_get_src" in text
        text = text.replace("Reader_get_src", f"{marker}\nReader_get_src", 1)
        frag.write_text(text, encoding="utf-8")
        _add_a_value(project)
        assert _cli("apply", cwd=project).returncode == 0
        assert marker in frag.read_text(encoding="utf-8")

    def test_a_reworded_refusal_is_not_clobbered(self, project: Path):
        """An author who reworded the message has said the text is theirs.

        The rewording deliberately KEEPS the `(choices: ...)` suffix. A
        reconciler keyed on the declared name alone would happily rewrite
        this, and the test would pass anyway if the rewording had dropped the
        suffix, because then there is nothing for a sloppy regex to match.
        What is under test is that the *whole* line must be jm's wording
        before jm treats the text as its own -- so the cost is stated too:
        this message keeps a stale choices list, in a file the author owns.
        """
        frag = project / FRAG
        frag.write_text(
            frag.read_text(encoding="utf-8").replace(
                "invalid t0 '%s' (choices: none, timecode)",
                "t0 was '%s' (choices: none, timecode)",
            ),
            encoding="utf-8",
        )
        _add_a_value(project)
        assert _cli("apply", cwd=project).returncode == 0
        text = frag.read_text(encoding="utf-8")
        assert "t0 was '%s' (choices: none, timecode)" in text
        # ...and the table underneath it is still reconciled, because that is
        # not the author's to own.
        assert '"sigmf",' in text


class TestANonAppendChangeIsGated:
    def test_reordering_warns_and_gates(self, project: Path):
        """Order IS the C int, so a reorder re-numbers data already written.

        jm cannot repair that by rewriting a table, so it is the one enum
        change that has to be loud rather than silently fixed.
        """
        assert _cli("apply", cwd=project).returncode == 0
        man = project / "just-makeit.toml"
        man.write_text(
            man.read_text(encoding="utf-8").replace(
                'values = ["none", "timecode"]',
                'values = ["timecode", "none"]',
            ),
            encoding="utf-8",
        )
        r = _cli("apply", cwd=project)
        blob = r.stdout + r.stderr
        assert "changed by more than an append" in blob
        assert "fail `jm status --check`" in blob


class TestTheReconcilerIsFormattingInsensitive:
    """Read the table as VALUES, never as text.

    These fragments are reformatted after every apply on a `c_style` project,
    so a GNU-indented table and jm's own render declare the same choices and
    never match as substrings. A text comparison would report drift on every
    formatted project and then rewrite the formatting to "fix" it -- the trap
    `_referenced_file_scope_decls` already documents one level up.
    """

    def test_same_values_differently_formatted_is_not_a_change(self):
        gnu = (
            "static const char *const _enum_A_k[] =\n"
            '  {\n    "x",\n    "y",\n    NULL,\n  };\n'
        )
        jm = 'static const char *const _enum_A_k[] = {"x", "y", NULL,};'
        text, changed, _ = _docsync.refresh_enum_tables(gnu, jm)
        assert changed == []
        assert text == gnu

    def test_the_lookup_helper_is_never_touched(self):
        """`_enum_index_*` shares the prefix and is not a table.

        The two copies are given DIFFERENT bodies on purpose. Passing the
        same text twice would pass with the name guard removed, since the
        reconciler compares values and a function declares none -- the test
        would then be asserting nothing about the guard it is named for.
        """
        helper = (
            "static int\n_enum_index_A(const char *const *tab, "
            "const char *s)\n{\n    return -1;\n}\n"
        )
        other = helper.replace("return -1;", 'return strcmp(tab[0], "x");')
        text, changed, _ = _docsync.refresh_enum_tables(helper, other)
        assert changed == []
        assert text == helper
