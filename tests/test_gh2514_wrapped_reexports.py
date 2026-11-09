"""gh-2514: a re-export past 79 columns is written wrapped, as ruff writes it.

A module's ``from .<leaf> import a, b, ...  # noqa: E402`` and its
``__all__ = [...]`` were single-line by contract, so a functions-only module
re-exporting ``gauss_hermite_quadrature`` and three more names wrote lines of
130 and 132 columns: E501 at 79, a ``ruff format`` rewrite even at ruff's
88, and every apply undid the formatter's wrap -- gh-1432's shape. Past the
column target jm now writes ruff's parenthesised form, one name per line with
a magic trailing comma and the ``# noqa: E402`` after the ``(``; below it,
the single line as before. Both ``ruff check --select I --fix`` and
``ruff format`` keep that form at any line length.

The ruff verdict is ``tests/test_gh1478_ruff_clean_scaffold.py`` (shapes
``functions_contract`` and ``wrapped_reexports``, and the platform-guarded
case), which needs the project environment. This file is the half
``make test`` runs: the text ruff's own ``--fix`` produced, asserted
literally, and every reader of the wrapped form -- add a name, remove one,
guard one, read back what a formatter wrapped, upgrade an older project's
single line -- each followed by an apply that changes nothing.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit._object import sort_reexports
from just_makeit._pyfmt import reflow_py, reflow_pyi

FNS = (
    "gauss_hermite_quadrature",
    "interpolate_between_samples",
    "render_waveform_description",
    "fill_coefficients_for_window",
)
OBJ = "--arg-type float --return-type float"
CALLS = [
    "new p",
    "module util",
    *(
        f"function {f} --module util --param x:double --return-type double"
        for f in FNS
    ),
    "module stages",
    *(
        f"object stage{n} --module stages {OBJ}"
        for n in (10, 2, 1, 11, 3, 4, 5)
    ),
    # 81 columns on one line beside 79: one wrapped, one not. And a third
    # name, which takes the package `__all__` to 81.
    *(f"object long_standalone_component_{n} {OBJ}" for n in (10, 2)),
    f"object shorter_one {OBJ}",
]


def _jm(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, f"jm {' '.join(argv)}\n{r.stdout}{r.stderr}"
    return r.stdout


def _scaffold(tmp: Path, calls: "list[str]" = CALLS) -> Path:
    first, *rest = (shlex.split(c) for c in calls)
    _jm(tmp, *first)
    root = tmp / "p"
    for argv in rest:
        _jm(root, *argv)
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "build" not in p.relative_to(root).parts
    }


def _converged(root: Path) -> None:
    """A second apply changes no file and ``status --check`` exits 0."""
    before = _tree(root)
    _jm(root, "apply")
    after = _tree(root)
    assert after == before, sorted(
        k
        for k in after.keys() | before.keys()
        if after.get(k) != before.get(k)
    )
    _jm(root, "status", "--check")


#: Each ``__init__.py``'s re-exports as ``ruff check --select I --fix
#: --line-length 79`` then ``ruff format`` leave them -- measured with jm's
#: pinned ruff on the single lines an older jm wrote, not with jm's code.
UTIL = (
    "from .util import (  # noqa: E402\n"
    "    fill_coefficients_for_window,\n"
    "    gauss_hermite_quadrature,\n"
    "    interpolate_between_samples,\n"
    "    render_waveform_description,\n"
    ")\n"
    "\n"
    "__all__ = [\n"
    '    "gauss_hermite_quadrature",\n'
    '    "interpolate_between_samples",\n'
    '    "render_waveform_description",\n'
    '    "fill_coefficients_for_window",\n'
    "]\n"
)
STAGES = (
    "from .stages import (  # noqa: E402\n"
    "    Stage1,\n    Stage2,\n    Stage3,\n    Stage4,\n    Stage5,\n"
    "    Stage10,\n    Stage11,\n"
    ")\n"
)
PACKAGE = (
    "from .long_standalone_component_2 import LongStandaloneComponent2"
    "  # noqa: E402\n"
    "from .long_standalone_component_10 import (  # noqa: E402\n"
    "    LongStandaloneComponent10,\n"
    ")\n"
    "from .shorter_one import ShorterOne  # noqa: E402\n"
    "\n"
    "__all__ = [\n"
    '    "LongStandaloneComponent10",\n'
    '    "LongStandaloneComponent2",\n'
    '    "ShorterOne",\n'
    "]\n"
)
INITS = {
    "src/p/util/__init__.py": UTIL,
    "src/p/stages/__init__.py": STAGES,
    "src/p/__init__.py": PACKAGE,
}


@pytest.fixture(scope="module")
def proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("gh2514"))


@pytest.mark.parametrize("rel", sorted(INITS))
def test_a_long_re_export_is_written_as_ruff_writes_it(
    proj: Path, rel: str
) -> None:
    text = (proj / rel).read_text(encoding="utf-8")
    assert INITS[rel] in text, f"{rel}:\n{text}"
    assert max(len(ln) for ln in text.splitlines()) <= 79, text


def test_the_scaffold_has_converged(proj: Path) -> None:
    _converged(proj)


def _ruff_format_form(text: str) -> str:
    """*text* with every wrapped re-export's marker after the ``)``: where
    ``ruff format`` puts it when it wraps a long single line itself."""
    out, moving = [], False
    for ln in text.split("\n"):
        if ln.endswith(" import (  # noqa: E402"):
            ln, moving = ln[: -len("  # noqa: E402")], True
        elif moving and ln == ")":
            ln, moving = ")  # noqa: E402", False
        out.append(ln)
    return "\n".join(out)


def _single_line_form(text: str) -> str:
    """*text* with every wrapped re-export and ``__all__`` on one line: what
    jm v0.124.0 wrote."""
    import re

    def _imp(m: "re.Match[str]") -> str:
        names = ", ".join(n.strip(" ,") for n in m.group(2).splitlines())
        return f"{m.group(1)}{names}  # noqa: E402"

    def _all(m: "re.Match[str]") -> str:
        names = ", ".join(n.strip(" ,") for n in m.group(1).splitlines())
        return f"__all__ = [{names}]"

    text = re.sub(
        r"^(from \.\w+ import )\(  # noqa: E402\n((?:    \w+,\n)+)\)",
        _imp,
        text,
        flags=re.M,
    )
    return re.sub(
        r'^__all__ = \[\n((?:    "\w+",\n)+)\]', _all, text, flags=re.M
    )


@pytest.mark.parametrize(
    "reflow", [_ruff_format_form, _single_line_form], ids=["format", "v0124"]
)
def test_a_reflowed_re_export_reads_back_to_jms_form(
    tmp_path: Path, reflow: "object"
) -> None:
    """Whatever wrapped -- or, from an older jm, did not wrap -- the
    statement, the next apply writes jm's form back, byte for byte, and
    then has nothing left to change."""
    root = _scaffold(tmp_path)
    for rel in INITS:
        path = root / rel
        jm = path.read_text(encoding="utf-8")
        moved = reflow(jm)  # type: ignore[operator]
        assert moved != jm, f"{reflow} left {rel} as it was"
        path.write_text(moved, encoding="utf-8")
    _jm(root, "apply")
    for rel, want in INITS.items():
        assert want in (root / rel).read_text(encoding="utf-8"), rel
    _converged(root)


def test_adding_and_removing_names_keeps_the_form(tmp_path: Path) -> None:
    """A name added to a wrapped statement, a name removed from one -- back
    under the target, so one line again -- and a wrapped package re-export
    removed whole, leaving no orphaned line behind."""
    root = _scaffold(tmp_path)
    util = root / "src/p/util/__init__.py"
    _jm(
        root,
        *shlex.split(
            "function apodize --module util --param x:double"
            " --return-type double"
        ),
    )
    text = util.read_text(encoding="utf-8")
    assert "from .util import (  # noqa: E402\n    apodize,\n" in text, text
    assert '__all__ = [\n    "gauss_hermite_quadrature",\n' in text, text
    assert '    "apodize",\n]\n' in text, text
    for f in (*FNS[1:], "apodize"):
        _jm(root, "remove", "function", f, "--module", "util", "--force")
    text = util.read_text(encoding="utf-8")
    assert "from .util import gauss_hermite_quadrature  # noqa: E402\n" in (
        text
    ), text
    assert '__all__ = ["gauss_hermite_quadrature"]\n' in text, text
    _jm(root, "remove", "object", "long_standalone_component_10", "--force")
    pkg = (root / "src/p/__init__.py").read_text(encoding="utf-8")
    assert "LongStandaloneComponent10" not in pkg, pkg
    assert (
        "from .long_standalone_component_2 import LongStandaloneComponent2"
        "  # noqa: E402\n"
        "from .shorter_one import ShorterOne  # noqa: E402\n\n"
        '__all__ = ["LongStandaloneComponent2", "ShorterOne"]\n'
    ) in pkg, pkg
    _converged(root)


def test_a_package_init_written_whole_is_wrapped(tmp_path: Path) -> None:
    """With no package ``__init__.py`` -- the author deleted it -- ``jm
    object`` writes the whole file from its template rather than splicing
    one, and that write is in jm's form too."""
    root = _scaffold(tmp_path, ["new p"])
    init = root / "src/p/__init__.py"
    init.unlink()
    _jm(root, *shlex.split(f"object long_standalone_component_10 {OBJ}"))
    text = init.read_text(encoding="utf-8")
    assert (
        "from .long_standalone_component_10 import (  # noqa: E402\n"
        "    LongStandaloneComponent10,\n"
        ")\n"
    ) in text, text
    assert max(len(ln) for ln in text.splitlines()) <= 79, text


GUARDED = (
    'if __import__("sys").platform in ("linux", "darwin"):\n'
    "    from .util import (  # noqa: E402\n"
    "        fill_coefficients_for_window,\n"
    "        gauss_hermite_quadrature,\n"
    "        interpolate_between_samples,\n"
    "        render_waveform_description,\n"
    "    )\n"
    "\n"
    "    __all__ += [\n"
    '        "fill_coefficients_for_window",\n'
    '        "gauss_hermite_quadrature",\n'
    '        "interpolate_between_samples",\n'
    '        "render_waveform_description",\n'
    "    ]\n"
)


def test_a_platform_guard_indents_every_line(tmp_path: Path) -> None:
    """gh-1463 lifts a restricted module's re-export into an ``if``: every
    line of the wrapped import and ``__all__ +=`` is indented, and the
    width counts the indent. Lifting the restriction puts the column-0
    form back."""
    root = _scaffold(tmp_path)
    frag = root / "modules/util.toml"
    plain = frag.read_text(encoding="utf-8")
    guarded = plain.replace(
        "[module.util]\n", '[module.util]\nplatforms = ["linux", "macos"]\n'
    )
    assert guarded != plain, plain
    frag.write_text(guarded, encoding="utf-8")
    _jm(root, "apply")
    util = root / "src/p/util/__init__.py"
    text = util.read_text(encoding="utf-8")
    assert GUARDED in text, text
    assert "\n__all__ = []\n" in text, text
    _converged(root)
    frag.write_text(plain, encoding="utf-8")
    _jm(root, "apply")
    text = util.read_text(encoding="utf-8")
    # The import back at column 0, wrapped; `__all__` keeps the order the
    # guard left it in (the merge keeps an existing `__all__`'s order).
    assert UTIL.split("\n\n")[0] in text, text
    assert '__all__ = [\n    "fill_coefficients_for_window",\n' in text, text
    assert "jm:platforms" not in text, text
    _converged(root)


def test_an_authors_wrapped_import_is_never_touched() -> None:
    """Only jm's exact shapes are jm's: a wrapped import with no marker, a
    comment inside, or a name on the opening line stays as written."""
    author = (
        "from .m import (\n    zeta,\n    alpha,\n)\n"
        "from .m import (  # noqa: E402\n    zeta,  # last\n    alpha,\n)\n"
        "from .m import (zeta,  # noqa: E402\n    alpha,\n)\n"
    )
    assert sort_reexports(author, {"m"}) == author


@pytest.mark.parametrize(
    "marker_after_paren", [True, False], ids=["isort", "format"]
)
def test_sort_reads_both_marker_positions(marker_after_paren: bool) -> None:
    names = sorted(FNS, reverse=True)
    body = "".join(f"    {n},\n" for n in names)
    if marker_after_paren:
        src = f"from .util import (  # noqa: E402\n{body})\n"
    else:
        src = f"from .util import (\n{body})  # noqa: E402\n"
    assert sort_reexports(src, {"util"}) == UTIL.split("\n\n")[0] + "\n"


# -- every other line that interpolates a name ------------------------------
#: A standalone component of 37 characters, with a long state name and a
#: console and a PEP 723 app over it, and a module of 15 (gh-2514): each
#: pushed a line of fixed text that names it past 79 columns. The module's
#: ``__init__.py`` header needs a longer one; the gh-1478 gate's
#: ``_LONG_NAMES`` carries it, and ``test_every_stub_header_wraps`` holds
#: the pass that wraps both.
LONG = "a_very_long_standalone_component_name"
LONG_CALLS = [
    "module pipeline_stages",
    f"object stage2 --module pipeline_stages {OBJ}",
    f"object {LONG} {OBJ} --state gain_value_of_the_stage:double:1.0",
    f"app --target console --object {LONG} --name a_long_console_tool_name",
    # One character more than the PEP 723 description holds at 79.
    f"object {LONG}_b {OBJ}",
    f"app --target pep723 --object {LONG}_b --name a_long_pep723_script_name",
]

#: Each file's text at those names, as jm now writes it: within 79 columns,
#: and in the layouts jm's pinned ruff keeps at 79 and at its default 88.
#: A key's ``#suffix`` only tells two entries for one file apart.
LONG_TEXT = {
    f"src/p/benchmarks/bench_{LONG}.py": (
        "Run standalone, from the project root:  python FILE, where FILE is\n"
        "\n"
        f"    src/p/benchmarks/bench_{LONG}.py\n"
    ),
    "src/p/pipeline_stages/pipeline_stages.pyi": (
        "# pipeline_stages/pipeline_stages.pyi — type stubs for the"
        " pipeline_stages C\n"
        "# extension.\n"
    ),
    "src/p/cli.py": (
        '"""p — a_long_console_tool_name: AVeryLongStandaloneComponentName'
        " command-line\n"
        "interface.\n"
    ),
    "src/p/cli.py#ctor": (
        "    obj = AVeryLongStandaloneComponentName(\n"
        "        gain_value_of_the_stage=args.gain_value_of_the_stage,\n"
        "    )\n"
    ),
    f"src/p/tests/test_{LONG}.py": (
        "        with AVeryLongStandaloneComponentName(\n"
        "            gain_value_of_the_stage=1.0,\n"
        "        ) as obj:\n"
    ),
    "a_long_pep723_script_name.py": (
        '        description=" ".join(\n'
        "            [\n"
        '                "p: AVeryLongStandaloneComponentNameB-powered stream'
        ' tool.",\n'
        "            ]\n"
        "        ),\n"
    ),
}


@pytest.fixture(scope="module")
def long_proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _scaffold(
        tmp_path_factory.mktemp("gh2514l"), ["new p", *LONG_CALLS]
    )


@pytest.mark.parametrize("key", sorted(LONG_TEXT))
def test_a_long_name_keeps_its_line_within_79(
    long_proj: Path, key: str
) -> None:
    text = (long_proj / key.split("#")[0]).read_text(encoding="utf-8")
    assert LONG_TEXT[key] in text, f"{key}:\n{text}"
    # The one line allowed past 79: a whitespace-free path, which E501
    # forgives, having nowhere to break.
    over = [
        ln for ln in text.splitlines() if len(ln) > 79 and " " in ln.strip()
    ]
    assert not over, over


def test_the_long_name_scaffold_has_converged(long_proj: Path) -> None:
    _converged(long_proj)


def test_the_pytest_benchmark_run_line_wraps(tmp_path: Path) -> None:
    root = _scaffold(
        tmp_path,
        ["new p --pytest --pytest-benchmark", f"object {LONG} {OBJ}"],
    )
    text = (root / f"src/p/benchmarks/bench_{LONG}.py").read_text(
        encoding="utf-8"
    )
    assert (
        "Run, from the project root:  pytest --benchmark-only FILE, where"
        " FILE is\n\n"
        f"    src/p/benchmarks/bench_{LONG}.py\n"
    ) in text, text


@pytest.mark.parametrize(
    "header",
    [
        "# h/h.pyi — type stubs for the {m} handle extension.",
        "# c.pyi — type stubs for the {m} capsule extension.",
        "# m.pyi — composer OO types for {m} (jm; gh-287).",
    ],
)
def test_every_stub_header_wraps(header: str) -> None:
    """The handle, capsule and composer stubs need TOML to scaffold, so the
    pass they all go through is held directly: a header naming a long
    module wraps at word boundaries, and a second pass changes nothing."""
    line = header.format(m="a_module_name_long_enough_to_pass_the_target")
    out = reflow_pyi(line + "\n")
    assert all(len(ln) <= 79 for ln in out.splitlines()), out
    assert out.replace("\n# ", " ").rstrip("\n") == line
    assert reflow_pyi(out) == out


def test_a_pep723_metadata_block_is_never_wrapped() -> None:
    meta = (
        "# /// script\n"
        "# dependencies ="
        ' ["a_very_long_project_name_that_does_not_fit_here==1.0", "numpy"]\n'
        "# ///\n"
    )
    assert reflow_py(meta) == meta
