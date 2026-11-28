"""gh-2846 -- a load served from `deferred_save`'s cache equals a disk load.

`save` folds every unfolding `load` did (group expansion, the ``extra_args``
rename, template instances, a ``pyproject.toml``-resolved version) before it
writes, and under `deferred_save` it caches that FOLDED form. The next
`load` returned the cache having run only the ``extra_args`` rename, so an
object's ``init_groups`` read back with no ``init_params``, and a template's
instances were absent: gh-764's rule that a cached load returns what a load
from disk would, broken for three of the four.

Now `_config._unfold` is every unfolding, called by the disk path and the
cached path alike. This file holds that two ways:

- for each unfolding `load` performs, a project that exercises it reads the
  same through a cached load as through a disk load; and
- the set of folds `save` performs is derived from its source and held
  equal to the cases here, so a fold added to `save` without a case
  (and so, by the pairing, an unfolding the cached path may lack) fails.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402

GROUP = """
[[group]]
name = "geom"
[[group.fields]]
name = "gain"
type = "double"
default = "1.0"
"""

#: A row of object `s` goes in its own fragment (`objects/s.toml`): a split
#: manifest refuses the same table declared in two files.
GROUP_ROW = """
[[s.init_groups]]
group = "geom"
prefix = "a"
"""

TEMPLATE = """
[template.conv]
params = ["elem"]
module = "cvt"
arg_type = "float"
return_type = "{elem}"

[[template.conv.instances]]
id = "conv_i16"
elem = "int16_t"

[[template.conv.instances]]
id = "conv_i8"
elem = "int8_t"
"""

SYNONYM_ROW = """
[[s.methods]]
name = "ctl"
arg_type = "void"
return_type = "float"

[[s.methods.extra_args]]
name = "k"
type = "int"
"""

#: fold function in `save` -> (text appended to the manifest, text appended
#: to `s`'s fragment, whether the project drops its manifest version so
#: `pyproject.toml` supplies it).
CASES = {
    "_without_group_expansion": (GROUP, GROUP_ROW, False),
    "_with_params_synonym": ("", SYNONYM_ROW, False),
    "_without_template_expansion": (TEMPLATE, "", False),
    "_without_deferred_version": ("", "", True),
}

#: What each unfolding makes of the project, read off a DISK load: the case
#: is only a test of the cached path if the disk path really unfolds it.
UNFOLDED = {
    "_without_group_expansion": lambda c: (
        [p["name"] for p in c["s"].get("init_params", [])] == ["a_gain"]
    ),
    "_with_params_synonym": lambda c: (
        "params" in c["s"]["methods"][0]
        and "extra_args" not in c["s"]["methods"][0]
    ),
    "_without_template_expansion": lambda c: (
        "conv_i16" in c and "conv_i8" in c
    ),
    "_without_deferred_version": lambda c: bool(c["project"].get("version")),
}


def _project(tmp_path: Path, extra: str, row: str, drop_version: bool) -> Path:
    r = run_cli("new", "p", "--object", "s", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    r = run_cli("module", "cvt", cwd=root)
    assert r.returncode == 0, r.stderr
    manifest = root / C.FILENAME
    text = manifest.read_text(encoding="utf-8")
    if drop_version:
        kept = [
            ln
            for ln in text.splitlines(keepends=True)
            if not ln.startswith("version = ")
        ]
        assert len(kept) == len(text.splitlines(keepends=True)) - 1
        text = "".join(kept)
    manifest.write_text(text + extra, encoding="utf-8")
    frag = root / "objects" / "s.toml"
    frag.write_text(frag.read_text(encoding="utf-8") + row, encoding="utf-8")
    return root


@pytest.mark.parametrize("fold", sorted(CASES))
def test_a_cached_load_equals_a_disk_load(tmp_path, fold):
    """After a `save` inside `deferred_save`, `load` returns what a load of
    the same manifest from disk does -- and keeps doing so across a second
    load-save round, as `apply`'s replay makes hundreds."""
    extra, row, drop_version = CASES[fold]
    root = _project(tmp_path, extra, row, drop_version)
    disk = C.load(root)
    assert UNFOLDED[fold](disk), disk
    with C.deferred_save():
        C.save(root, C.load(root))
        cached = C.load(root)
        assert cached == disk
        C.save(root, cached)
        assert C.load(root) == disk


def test_every_fold_save_performs_has_a_case():
    """The folds are derived from `save`'s source, not listed here: a new
    ``_without_*`` / ``_with_*`` call there needs an unfolding in `_unfold`
    and a case above."""
    tree = ast.parse(inspect.getsource(C.save).lstrip())
    folds = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id.startswith(("_without_", "_with_"))
    }
    assert folds == set(CASES) == set(UNFOLDED)
