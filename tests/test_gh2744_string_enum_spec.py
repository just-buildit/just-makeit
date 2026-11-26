"""gh-2744 -- ``--init-param 'k:string_enum:lo,hi=lo'`` crashed with a traceback.

The author meant ``=lo`` as the default. ``--init-param`` splits
``name:type:default``, so ``hi=lo`` is read as the choice ``hi`` bound to the
C constant ``lo``, and ``lo`` is the only choice without one. The reader
``string_enum_constants`` then indexed a constant that is not there and
raised ``IndexError``.

The fix refuses a spec that mixes bound and unbound choices at the flag
parser, naming the choices and the spelling of a default. The reader is
unchanged: a refusal at the one place a spec enters beats a traceback at the
one place it is read.

Each test drives jm through ``run_cli`` in this process, the way a user does.
"""

from _jmrun import run_cli


def _scaffold(tmp_path):
    r = run_cli("new", "pp", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return tmp_path / "pp"


def test_mixed_bound_and_unbound_choices_are_refused_by_name(tmp_path):
    proj = _scaffold(tmp_path)
    r = run_cli(
        "object", "k", "--init-param", "kind:string_enum:lo,hi=lo", cwd=proj
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "IndexError" not in r.stderr, r.stderr
    # The message names the unbound choice, the bound one, and the spelling
    # that gives a default.
    assert "'lo' bind none" in r.stderr, r.stderr
    assert "'hi=lo' bind a constant" in r.stderr, r.stderr
    assert "name:string_enum:lo,hi:lo" in r.stderr, r.stderr


def test_default_after_a_third_colon_is_accepted(tmp_path):
    proj = _scaffold(tmp_path)
    r = run_cli(
        "object", "k", "--init-param", "kind:string_enum:lo,hi:lo", cwd=proj
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_all_bound_or_all_unbound_still_accepted(tmp_path):
    proj = _scaffold(tmp_path)
    r = run_cli(
        "object",
        "k",
        "--init-param",
        "kind:string_enum:lo,hi",
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def _hand_edit(proj, old, new):
    """Replace *old* with *new* in every project TOML, as a hand edit would.

    Returns the number of files that held *old*, so a test can assert the
    edit landed rather than pass on an unchanged manifest.
    """
    hits = 0
    for path in proj.rglob("*.toml"):
        text = path.read_text(encoding="utf-8")
        if old in text:
            path.write_text(text.replace(old, new), encoding="utf-8")
            hits += 1
    return hits


def test_hand_edited_manifest_is_refused_by_apply_with_the_same_sentence(
    tmp_path,
):
    proj = _scaffold(tmp_path)
    r = run_cli(
        "object", "k", "--init-param", "kind:string_enum:lo,hi:lo", cwd=proj
    )
    assert r.returncode == 0, r.stdout + r.stderr
    hits = _hand_edit(proj, 'string_enum:lo,hi"', 'string_enum:lo,hi=lo"')
    assert hits >= 1, "the hand edit did not land; the test proves nothing"
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "IndexError" not in r.stderr, r.stderr
    assert "string_enum choices must all bind a C constant" in r.stderr, (
        r.stderr
    )
    assert "'lo' bind none" in r.stderr, r.stderr
