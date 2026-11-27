"""gh-2555 -- ``status --check`` passed over a stale module constructor.

A module object's constructor wrapper carries its create-failure block: the
exception class and message named by ``create_error`` /
``create_error_message`` in the manifest. ``apply`` keeps the author's
existing function bodies in a sacred module fragment, and this block is
jm's glue, not author code. So an edit to the translation in the manifest
left the fragment raising the old exception while the stub moved on, and
``status --check`` reported the project as up to date.

The fix makes ``status`` report that difference as a stale fragment, so
``--check`` exits 1 and names the file. It does not change what ``apply``
writes.

Each test drives jm through ``run_cli`` in this process, as a user would.
"""

from _jmrun import run_cli


def _scaffold_module_object(tmp_path):
    proj = tmp_path / "i3"
    r = run_cli("new", "i3", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    for args in (
        ("module", "dsp"),
        ("object", "acc", "--module", "dsp", "--init-param", "mode:int:0"),
        (
            "error",
            "acc",
            "--module",
            "dsp",
            "--category",
            "ValueError",
            "--message",
            "old message",
        ),
    ):
        r = run_cli(*args, cwd=proj)
        assert r.returncode == 0, (args, r.stdout + r.stderr)
    return proj


def _hand_edit(proj, pairs):
    """Replace each (old, new) in every project TOML, as an author would.

    Returns the count of files that held an *old*, so a test can refuse to
    pass on an edit that never landed.
    """
    hits = 0
    for path in proj.rglob("*.toml"):
        text = path.read_text(encoding="utf-8")
        new_text = text
        for old, new in pairs:
            new_text = new_text.replace(old, new)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            hits += 1
    return hits


def test_apply_rewrites_a_module_constructors_stale_failure_block(tmp_path):
    proj = _scaffold_module_object(tmp_path)
    hits = _hand_edit(
        proj,
        [
            (
                'create_error_message = "old message"',
                'create_error_message = "new message"',
            ),
            ('create_error = "ValueError"', 'create_error = "RuntimeError"'),
        ],
    )
    assert hits >= 1, "the manifest edit did not land; the test proves nothing"
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    fragment = proj / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    text = fragment.read_text(encoding="utf-8")
    # The binding now raises what the stub documents.
    assert "PyExc_RuntimeError" in text, text
    assert "new message" in text and "old message" not in text, text
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr


def test_apply_keeps_an_authors_own_function_in_the_fragment(tmp_path):
    """The guard against over-reach: the constructor is jm's, but a function
    the author wrote in the same sacred fragment must survive `apply`.
    """
    proj = _scaffold_module_object(tmp_path)
    fragment = proj / "native" / "src" / "dsp" / "dsp_ext_acc.c"
    author = (
        "\nstatic int acc_authors_own_helper(int x)\n"
        "{\n    return x * 7 + 3; /* hand-written, must survive */\n}\n"
    )
    fragment.write_text(
        fragment.read_text(encoding="utf-8") + author, encoding="utf-8"
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "return x * 7 + 3; /* hand-written, must survive */" in (
        fragment.read_text(encoding="utf-8")
    )
