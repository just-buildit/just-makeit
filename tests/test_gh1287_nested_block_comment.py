"""gh-1287: generated C must never open `/*` inside a block comment.

`_composer.py`'s `_attach_bytes` banner read *"into an owned `*dst/*n_dst`"*.
To a C compiler the `/*` in `/*n_dst` opens a second block comment inside the
first, which is what an unterminated comment looks like, so every one of them
warns::

    wfm_compose_ext.c:178:8: warning: '/*' within block comment [-Wcomment]

**The reason this is a gate and not a one-line fix.** It was in a *template*,
so one line of prose became a warning in every downstream project that
generates a composer, on every build -- doppler counted three per coverage
build. And the consumer cannot fix it where they see it: `jm apply` owns the
generated file and reverts an edit to it, which is how doppler traced it back
here rather than patching locally. jm's own suite could not see it at all,
because nothing compiled the file and nothing read its comments; the only
place it showed up was a consumer's build log, which is the worst place to
find anything.

**Why the corpus is rendered C and not jm's source.** The obvious cheaper gate
-- scan the templates and the C-emitting string literals in `src/` -- was
measured first and is unusable in both directions. Under it, all 19 `/*` hits
in `templates/` are false: `/*<<token>>*/` is jm's own placeholder form, a
complete comment that disappears at render time. And three of the four hits in
`src/**/*.py` are false too, being prose *about* C comment syntax in a Python
docstring (`_docstring.py` explaining `/**<`). A gate needing an exemption list
that long is a gate someone turns off. The artifact is what the compiler reads,
so the artifact is what gets read here.

The scanner is the gate, so `TestTheScannerItself` sabotages it directly: a
scanner that silently matched nothing would keep reporting green forever.

**It is deliberately not `_docsync._code_mask`,** which walks the same four
states for `jm apply`'s structural scans. That is the one place reuse is the
wrong instinct: a gate whose oracle comes from the pipeline it is checking is
blind to every fault in that pipeline, so a lexing bug in `_code_mask` would
silently be a blind spot here rather than a finding. The two are peers on
purpose, and they check each other.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

# Every C file kind jm writes into a project. Derived from the tree rather
# than listed, so a new generated file is covered the day it is generated.
_C_SUFFIXES = (".c", ".h")


def nested_block_comment_hits(text: str) -> list[tuple[int, str]]:
    """Every ``/*`` that opens while a block comment is already open.

    Returns ``(line_number, stripped_source_line)`` for each, one-indexed.

    This walks the same four lexical states the preprocessor does -- code,
    ``//`` comment, ``/* */`` comment, string/char literal -- rather than
    scanning for the two characters, and the literal state is the half that
    earns its keep. Generated C carries author prose inside string literals
    (`PyDoc_STRVAR` bodies lifted from Doxygen by `_docstring.py`), so an
    author who writes ``/* ... */`` in a header comment would trip a naive
    scanner on a line no compiler objects to. A gate that cries wolf on the
    author's own text gets disabled, and then it is not a gate.

    Parameters
    ----------
    text
        C source, as rendered -- not a template. `/*<<token>>*/` is jm's
        placeholder spelling and is a nested open until it is substituted.

    Returns
    -------
    list of (int, str)
        One entry per offending ``/*``, in source order. Empty is a pass.

    Examples
    --------
    >>> nested_block_comment_hits("/* an owned *dst/*n_dst */\\n")
    [(1, '/* an owned *dst/*n_dst */')]
    >>> nested_block_comment_hits('const char *s = "/*";\\n')
    []
    >>> nested_block_comment_hits("/* fine */ /* also fine */\\n")
    []
    """
    hits: list[tuple[int, str]] = []
    lines = text.splitlines()
    i, n, line = 0, len(text), 1
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
        elif text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
        elif ch in "\"'":
            quote, i = ch, i + 1
            while i < n and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                elif text[i] == "\n":
                    line += 1
                i += 1
            i += 1
        elif text.startswith("/*", i):
            i += 2
            while i < n and not text.startswith("*/", i):
                if text[i] == "\n":
                    line += 1
                elif text.startswith("/*", i):
                    hits.append((line, lines[line - 1].strip()))
                    i += 1
                i += 1
            i += 2
        else:
            i += 1
    return hits


def assert_no_nested_block_comments(root: Path) -> None:
    """Fail if any C or H file under *root* nests a block comment.

    Shared with `tests/test_examples.py`, which applies it to every example
    project's scaffolded tree -- the widest corpus jm has, and one that grows
    whenever an example is added. One scanner, two corpora; a second
    implementation of this check is how the two would come to disagree.
    """
    offenders = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _C_SUFFIXES:
            continue
        for lineno, src in nested_block_comment_hits(
            path.read_text(encoding="utf-8", errors="replace")
        ):
            offenders.append(f"{path.relative_to(root)}:{lineno}: {src}")
    assert not offenders, (
        "`/*` inside a block comment in generated C -- every compiler warns"
        " (-Wcomment), in every downstream build, on a file the consumer"
        " cannot fix because `jm apply` owns it:\n  " + "\n  ".join(offenders)
    )


class TestTheScannerItself:
    """An unarmed scanner reports green on everything. Prove it is armed."""

    def test_finds_the_gh1287_offender(self):
        banner = (
            "/* Copy a Python bytes (0/1 pattern) or None into an owned\n"
            " * *dst/*n_dst (one shared coercer). */\n"
        )
        assert nested_block_comment_hits(banner) == [
            (2, "* *dst/*n_dst (one shared coercer). */")
        ]

    def test_the_shipped_wording_is_clean(self):
        assert not nested_block_comment_hits(
            "/* Copy a Python bytes (0/1 pattern) or None\n"
            " * into an owned *dst and *n_dst (one shared coercer). */\n"
        )

    def test_reports_every_offender_not_just_the_first(self):
        assert len(nested_block_comment_hits("/* a /* b /* c */")) == 2

    def test_a_slash_star_in_a_string_literal_is_not_one(self):
        """The author's prose reaches generated C inside `PyDoc_STRVAR`."""
        assert not nested_block_comment_hits(
            'PyDoc_STRVAR(d, "write /* like this */ in C");\n'
        )

    def test_a_slash_star_in_a_line_comment_is_not_one(self):
        assert not nested_block_comment_hits("// see /* above\nint x;\n")

    def test_an_escaped_quote_does_not_swallow_the_file(self):
        """A mis-lexed literal would run to EOF and hide every later hit."""
        assert nested_block_comment_hits(
            'char *q = "he said \\"hi\\"";\n/* a /* b */\n'
        ) == [(2, "/* a /* b */")]

    def test_a_quote_inside_a_comment_is_not_a_literal(self):
        """`don't` in a banner used to open a char literal and eat the rest."""
        assert nested_block_comment_hits(
            "/* don't do this */\n/* a /* b */\n"
        ) == [(2, "/* a /* b */")]


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()"]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


# The three `kind`-bearing module faces are manifest-only -- there is no
# `jm composer` command -- so they reach the tree the way a real project's do,
# through `jm apply`. The composer is the shape gh-1287 was found in, and its
# `_attach_bytes` banner is emitted for every composer source, so `bytes` is
# not decoration here: it is the one field that makes the fixture able to fail.
_KIND_MODULES = """
[module.hand]
kind = "handle"
backing = "b"
header = "b/b.h"
type_name = "H"
close_fn = "b_close"
create_fn = "b_open"
create_args = []

[module.cap]
kind = "capsule"
backing = "b"
header = "b/b.h"

[module.mix]
kind = "composer"
backing = "mix"
composes = ["clip"]

[module.mix.source]
object = "clip"
struct = "clip_t"
type_name = "Clip"

[[module.mix.source.fields]]
name = "gain"
type = "double"
default = "1.0"

[[module.mix.source.fields]]
name = "bits"
bytes = true

[module.mix.segment]
type_name = "Track"
struct = "track_t"
sources = "multi"

[[module.mix.segment.fields]]
name = "dur"
type = "size_t"
default = "4"

[module.mix.oo]
composer_type_name = "Mix"
"""


@pytest.fixture(scope="module")
def wide_project(tmp_path_factory) -> Path:
    """One project carrying every C-emitting face jm has.

    Compiler-free on purpose: this is a text property, so it belongs in the
    fast suite that runs on every machine rather than behind the toolchain
    guard `test-examples` sits behind.

    The shapes are not decoration. A fixture holding only plain objects would
    have passed on the day gh-1287 was filed, which is the failure mode
    gh-1205 already cost once -- a registration-free sweep over *files* is not
    coverage over *shapes*, and it reports green for whatever it never
    generated.
    """
    tmp = tmp_path_factory.mktemp("nbc")
    assert _cli("new", "wide", cwd=tmp).returncode == 0
    root = tmp / "wide"
    for args in (
        ("object", "cplx", "--state", "gain:double:1.0"),
        ("object", "arr", "--state", "taps:float[8]"),
        ("object", "fast", "--perf", "--state", "g:double:1.0"),
        ("object", "clip", "--state", "gain:double:1.0"),
        ("module", "dsp"),
        ("object", "filt", "--module", "dsp"),
        (
            "function",
            "scale",
            "--module",
            "dsp",
            "--param",
            "x:double",
            "--return-type",
            "double",
        ),
    ):
        r = _cli(*args, cwd=root)
        assert r.returncode == 0, f"{args}\n{r.stdout}{r.stderr}"
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _KIND_MODULES,
        encoding="utf-8",
    )
    r = _cli("apply", cwd=root)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    return root


class TestNoGeneratedCNestsABlockComment:
    def test_the_fixture_covers_every_face(self, wide_project: Path):
        """A sweep that generated nothing passes for the wrong reason."""
        emitted = {
            p.relative_to(wide_project).as_posix()
            for p in wide_project.rglob("*")
            if p.is_file() and p.suffix in _C_SUFFIXES
        }
        for expected in (
            "native/src/cplx/cplx_ext.c",  # standalone object
            "native/src/dsp/dsp_ext_filt.c",  # object in a module
            "native/src/dsp/scale.c",  # module-level function
            "native/inc/jm_perf.h",  # --perf
            "native/src/hand/hand_ext.c",  # kind = "handle"
            "native/src/cap/cap_ext.c",  # kind = "capsule"
            "native/src/mix/mix_ext.c",  # kind = "composer" (gh-1287)
        ):
            assert expected in emitted, sorted(emitted)

    def test_sweep(self, wide_project: Path):
        assert_no_nested_block_comments(wide_project)
