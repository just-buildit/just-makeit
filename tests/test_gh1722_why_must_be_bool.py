"""gh-1722: a ``why`` switch that is not ``true``/``false`` is refused at load.

``[module.X.json] from_json_why`` / ``from_file_why`` and a module function's
``why`` mark an EXISTING function as taking a trailing ``const char **why``
(gh-1706). A function name is the natural wrong guess, and a string is
truthy: jm accepted ``from_json_why = "dp_wfm_compose_from_json_why"``,
rendered a two-argument call to a one-argument reader, and the mistake
surfaced in the C compiler. Found by doppler's dry run of #1717.

And the ``@param why`` line jm adds to an authored docblock carries jm's own
sentence, since jm, not the author, defines what the parameter means.
"""

# gh-1591: this file's expectations spell jm's bare derived names, so its
# project opts out of the prefix `jm new` now defaults to.

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "w"
    root.mkdir()
    r = run_cli("new", "q", "--no-c-prefix", cwd=root)
    assert r.returncode == 0, r.stderr
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    return proj


def _function(proj: Path, *extra: str) -> None:
    r = run_cli(
        "function",
        "fb",
        "--module",
        "m",
        "--param",
        "n:size_t",
        "--return-type",
        "int",
        "--check-return",
        *extra,
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr


def _manifest(proj: Path) -> Path:
    """The file holding ``[module.m]``: the central manifest or a fragment."""
    frag = proj / "modules" / "m.toml"
    return frag if frag.exists() else proj / "just-makeit.toml"


def _out(r) -> str:
    return r.stdout + r.stderr


def test_a_json_why_naming_a_function_is_refused(tmp_path):
    proj = _project(tmp_path)
    m = _manifest(proj)
    m.write_text(
        m.read_text()
        + '\n[module.m.json]\nfrom_json_why = "dp_from_json_why"\n'
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 1, _out(r)
    assert "[module.m.json] from_json_why = 'dp_from_json_why'" in _out(r)
    assert "must be true or false" in _out(r)
    assert "Name the function in from_json_fn" in _out(r)


def test_a_function_why_that_is_not_a_bool_is_refused(tmp_path):
    proj = _project(tmp_path)
    _function(proj, "--why")
    m = _manifest(proj)
    text = m.read_text()
    assert re.search(r"^why = true$", text, re.M), text
    m.write_text(re.sub(r"^why = true$", 'why = "yes"', text, flags=re.M))
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 1, _out(r)
    assert "fb: why = 'yes' must be true or false" in _out(r)


def test_a_bool_why_still_loads(tmp_path):
    """The refusal is the type, not the key: ``true`` applies as before."""
    proj = _project(tmp_path)
    _function(proj, "--why")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, _out(r)


def test_the_injected_why_line_says_what_jm_means(tmp_path):
    """An authored docblock gains a documented ``@param why``, not a bare one."""
    proj = _project(tmp_path)
    _function(proj)
    hdr = next((proj / "native" / "inc").rglob("m_core.h"))
    src = hdr.read_text()
    decl = re.search(r"^int\s+fb\s*\(size_t n\);$", src, re.M)
    assert decl, src
    hdr.write_text(
        src[: decl.start()]
        + "/**\n * @brief Count the bits in a spec.\n *\n"
        + " * @param n  How many to count.\n */\n"
        + src[decl.start() :]
    )
    m = _manifest(proj)
    text = m.read_text()
    m.write_text(
        re.sub(r'^(name = "fb")$', r"\1\nwhy = true", text, flags=re.M)
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, _out(r)

    block = hdr.read_text()
    assert "const char **why" in block, block
    assert " * @param n  How many to count." in block, block
    assert (
        " * @param why  Set to a static reason on refusal; may be NULL."
        in block
    ), block
