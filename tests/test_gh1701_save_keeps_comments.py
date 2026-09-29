"""gh-1701: a mutating save keeps the comments of everything it did not change.

`_config._sync` skipped a key whose value was unchanged, so an untouched
table kept its text. A key whose value DID change was assigned whole, and
tomlkit re-rendered it from the plain dict: every comment inside it was
deleted and every row re-laid out. The value that changes is almost always
a table array or a table holding one -- one new method is a changed
``methods`` list -- so the comments on every sibling row went with it. On
doppler at 0.92.2, appending one method to each object deleted 422 comment
lines across 60 ``objects/*.toml``; editing one ``doc`` on one composer
``source.fields`` row deleted 15.

The fix syncs a changed table array row by row, and a row that exists on
both sides key by key. That alone moves comments to the wrong row, because
tomlkit files a comment under the table ABOVE it: the comment introducing
row ``b`` lives in row ``a``'s body. So each test here pins the exact text,
not just that the comment lines survive -- a comment left above the wrong
header is worse than a lost one.

Every shape a save rewrites is covered: an object's table array in the
manifest and in an ``objects/*.toml`` fragment, a module table in a
``modules/*.toml`` fragment, a composer ``source`` sub-table with ``[[x]]``
rows and with an inline ``x = [{...}]`` array, and the top-level ``[[enum]]``
array.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402

PROJECT = '[project]\nname = "p"\nversion = "0.1.0"\n\n'

OBJECT = """\
# The engine: why it exists.
[eng]
arg_type = "float"
return_type = "float"
# why the methods below exist
[[eng.methods]]
name = "a"
arg_type = "float"

# why b exists
[[eng.methods]]
name = "b"
arg_type = "float"

# why c exists
[[eng.methods]]
name = "c"
arg_type = "float"

# The next section: why it exists.
[eng2]
arg_type = "float"
"""


def _save(root: pathlib.Path, edit) -> str:
    """Load *root*, apply *edit* to the cfg, save, and return the text."""
    with contextlib.redirect_stderr(io.StringIO()):
        cfg = C.load(root)
        edit(cfg)
        C.save(root, cfg)
    return (root / C.FILENAME).read_text()


def _project(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    (tmp_path / C.FILENAME).write_text(PROJECT + body)
    return tmp_path


def _methods(cfg: dict) -> list:
    return cfg["eng"]["methods"]


def test_editing_one_row_changes_only_that_line(tmp_path) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        _methods(cfg)[1]["arg_type"] = "double"

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        'name = "b"\narg_type = "float"', 'name = "b"\narg_type = "double"'
    )


def test_a_new_key_lands_in_its_row_not_under_the_next_rows_comment(
    tmp_path,
) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        _methods(cfg)[0]["doc"] = "A."

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        'name = "a"\narg_type = "float"\n',
        'name = "a"\narg_type = "float"\ndoc = "A."\n',
    )


def test_a_new_key_lands_above_the_first_rows_comment(tmp_path) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        cfg["eng"]["mutable"] = "true"

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        'return_type = "float"\n# why the methods',
        'return_type = "float"\nmutable = "true"\n# why the methods',
    )


def test_appending_a_row_keeps_the_next_sections_comment_above_it(
    tmp_path,
) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        _methods(cfg).append({"name": "z", "arg_type": "float"})

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        'name = "c"\narg_type = "float"\n',
        'name = "c"\narg_type = "float"\n\n'
        '[[eng.methods]]\nname = "z"\narg_type = "float"\n',
    )


def test_inserting_a_middle_row_leaves_each_comment_on_its_row(
    tmp_path,
) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        _methods(cfg).insert(1, {"name": "n", "arg_type": "float"})

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        "# why b exists\n",
        '[[eng.methods]]\nname = "n"\narg_type = "float"\n\n# why b exists\n',
    )


def test_deleting_a_middle_row_takes_its_comment_with_it(tmp_path) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        cfg["eng"]["methods"] = [m for m in _methods(cfg) if m["name"] != "b"]

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        '# why b exists\n[[eng.methods]]\nname = "b"\narg_type = "float"\n\n',
        "",
    )


def test_deleting_the_first_row_takes_its_comment_with_it(tmp_path) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        cfg["eng"]["methods"] = [m for m in _methods(cfg) if m["name"] != "a"]

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        "# why the methods below exist\n"
        '[[eng.methods]]\nname = "a"\narg_type = "float"\n\n',
        "",
    )


def test_deleting_the_last_row_keeps_the_next_sections_comment(
    tmp_path,
) -> None:
    root = _project(tmp_path, OBJECT)

    def edit(cfg):
        cfg["eng"]["methods"] = [m for m in _methods(cfg) if m["name"] != "c"]

    text = _save(root, edit)
    assert text == PROJECT + OBJECT.replace(
        '# why c exists\n[[eng.methods]]\nname = "c"\narg_type = "float"\n\n',
        "",
    )


def test_an_objects_fragment_keeps_its_comments(tmp_path) -> None:
    (tmp_path / C.FILENAME).write_text(
        'include = ["objects/*.toml"]\n\n' + PROJECT
    )
    (tmp_path / "objects").mkdir()
    frag = tmp_path / "objects" / "eng.toml"
    body = OBJECT.split("# The next section")[0].rstrip("\n") + "\n"
    frag.write_text(body)

    def edit(cfg):
        _methods(cfg).append({"name": "z", "arg_type": "float"})

    _save(tmp_path, edit)
    assert frag.read_text() == body + (
        '\n[[eng.methods]]\nname = "z"\narg_type = "float"\n'
    )


MODULE = """\
[module.filt]
objects = ["fir"]

# why f exists
[[module.filt.functions]]
name = "f"
arg_type = "float"
return_type = "float"

# why g exists
[[module.filt.functions]]
name = "g"
arg_type = "float"
return_type = "float"
"""


def test_a_modules_fragment_keeps_its_comments(tmp_path) -> None:
    (tmp_path / C.FILENAME).write_text(
        'include = ["modules/*.toml"]\n\n' + PROJECT
    )
    (tmp_path / "modules").mkdir()
    frag = tmp_path / "modules" / "filt.toml"
    frag.write_text(MODULE)

    def edit(cfg):
        cfg["module"]["filt"]["functions"][0]["doc"] = "F."

    _save(tmp_path, edit)
    assert frag.read_text() == MODULE.replace(
        'name = "f"\narg_type = "float"\nreturn_type = "float"\n',
        'name = "f"\narg_type = "float"\nreturn_type = "float"\ndoc = "F."\n',
    )


COMPOSER = """\
[module.wfm]
kind = "composer"
backing = "wfm"

[module.wfm.source]
object = "wfm_synth"
struct = "wfm_source_t"
# Fields that accept a (lo, hi) pair drawn uniformly each repeat.
ranged = [
  { name = "freq", flag = "WFM_RANGE_FREQ" },  # the carrier
  { name = "snr", flag = "WFM_RANGE_SNR" },
]

# why freq lives on the source
[[module.wfm.source.fields]]
name = "freq"
type = "double"

# gh-1184: the owned array lives at `.bits`/`.len`.
[[module.wfm.source.fields]]
name = "bits"
type = "uint8_t*"
bytes = true
c_ptr = "sync.bits"
c_len = "sync.len"
"""


def _source(cfg: dict) -> dict:
    return cfg["module"]["wfm"]["source"]


def test_a_composer_source_row_edit_keeps_every_comment(tmp_path) -> None:
    """The issue's own repro: one ``doc`` on one ``source.fields`` row."""
    root = _project(tmp_path, COMPOSER)

    def edit(cfg):
        _source(cfg)["fields"][0]["doc"] = "MUTATED"

    text = _save(root, edit)
    assert text == PROJECT + COMPOSER.replace(
        'name = "freq"\ntype = "double"\n',
        'name = "freq"\ntype = "double"\ndoc = "MUTATED"\n',
    )


def test_an_inline_row_array_stays_inline_with_its_comments(
    tmp_path,
) -> None:
    root = _project(tmp_path, COMPOSER)

    def edit(cfg):
        _source(cfg)["ranged"][1]["flag"] = "WFM_RANGE_SNR_DB"

    text = _save(root, edit)
    assert text == PROJECT + COMPOSER.replace(
        '"WFM_RANGE_SNR"', '"WFM_RANGE_SNR_DB"'
    )


ENUMS = """\
# why wave exists
[[enum]]
name = "wave"
values = ["tone", "noise"]

# why mode exists
[[enum]]
name = "mode"
values = ["auto", "fs"]

[eng]
arg_type = "float"
"""


def test_a_top_level_enum_array_keeps_its_comments(tmp_path) -> None:
    root = _project(tmp_path, ENUMS)

    def edit(cfg):
        cfg["enum"][1]["values"] = ["auto", "fs", "ebno"]

    text = _save(root, edit)
    assert text == PROJECT + ENUMS.replace(
        '["auto", "fs"]', '["auto", "fs", "ebno"]'
    )
