"""gh-1694: a composer seam named with the prefix is the author's, not jm's.

A composer source hands its straight-C seams back to the project by name:
``[module.X.source.generates] bridge_fn`` (and its optional
``bridge_error_fn``) and each ``[[module.X.source.computed]] fn``. jm derives
none of those names -- the keys are required and author-named
(``_csym.AUTHOR_NAMED_KEYS``) -- but it DECLARES each of them, in the
module's generated ``<cname>_bridge.h`` (gh-998).

``_csym.renames`` reads the replay's headers for every name that starts with
a derivation stem. So under ``c_prefix = "dp"`` beside a component ``wfm``,
``bridge_fn = "wfm_source_to_synth"`` was no row, but the same function
renamed to ``dp_wfm_source_to_synth`` -- doppler's move of its hand-named
exports onto ``dp_`` -- became the rename ``wfm_source_to_synth ->
dp_wfm_source_to_synth``. The collisions guard then refused the author's own
definition of the very function the key names ("already declares ... the
name c_prefix derives from component `wfm`"), in `apply` and `jm upgrade`.

The fix: a name a rendered header declares only because an author-named key
spells it is not derived THERE (``_csym._echoed``, from
``_composer.seam_fns`` -- the one list ``render_bridge_h`` renders). Per
header, not per name: the same name declared where jm does derive it (a
computed ``fn`` naming a sibling's lifecycle function) keeps its row.

GATE: a project built by jm with every seam key spelled ``dp_wfm_*``, each
      defined in the author's C and declared in a C test, `apply`s and
      `upgrade`s with exit 0; the replay's rename table holds no seam name
      while it still holds ``wfm``'s derived names; and a seam that names a
      derived function keeps that function's row.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _apply
from just_makeit import _config as C
from just_makeit import _csym
from just_makeit import _textio
from just_makeit._new import run as new_run

P = "dp"

#: Every seam key, each spelled with the prefix of component `wfm` -- the
#: shape doppler's #1565 moved to. None of these is a name jm derives.
SEAMS = {
    "bridge_fn": f"{P}_wfm_source_to_synth",
    "bridge_error_fn": f"{P}_wfm_source_why",
    "fn": f"{P}_wfm_source_length",
}

COMPOSER_TOML = """
[module.wfm_compose]
kind = "composer"
backing = "wfm_compose"
header = "q/wfm_compose/wfm_compose.h"
composes = ["wfm"]

[module.wfm_compose.source]
object = "wfm"
struct = "wfm_source_t"
type_name = "Synth"
fields = [{{ name = "freq", type = "double", default = "0.0" }}]

[module.wfm_compose.source.generates]
generator = "wfm"
bridge_fn = "{bridge_fn}"
bridge_error_fn = "{bridge_error_fn}"

[[module.wfm_compose.source.computed]]
name = "length"
type = "double"
fn = "{fn}"

[module.wfm_compose.segment]
type_name = "Segment"
struct = "wfm_segment_t"
sources = "multi"
fields = [{{ name = "fs", type = "double", default = "1e6" }}]
"""

#: The author's definitions, in a hand-written file beside the component --
#: doppler's `native/src/wfm/wfm_synth_bridge.c`.
BRIDGE_C = """#include "q/wfm/wfm_core.h"

{P}_wfm_state_t *{bridge_fn}(const void *src, double fs)
{{
    (void)src;
    (void)fs;
    return 0;
}}

const char *{bridge_error_fn}(const void *src, double fs)
{{
    (void)src;
    (void)fs;
    return 0;
}}

double {fn}(const void *src)
{{
    (void)src;
    return 0.0;
}}
"""

#: A C test re-declaring one of them, as doppler's does.
TEST_C = "double {fn}(const void *src);\nint main(void) {{ return 0; }}\n"


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _project(where: Path, seams: dict) -> Path:
    """A prefixed project built by running jm: component `wfm`, then a
    composer over it whose seams are named by *seams*, defined by hand."""
    root = where / "q"
    new_run("q", root, c_prefix=P)
    _ok(
        "object",
        "wfm",
        "--state",
        "gain:double:1.0",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    toml = root / C.FILENAME
    _textio.write_text(toml, toml.read_text() + COMPOSER_TOML.format(**seams))
    _textio.write_text(
        root / "native" / "src" / "wfm" / "wfm_synth_bridge.c",
        BRIDGE_C.format(P=P, **seams),
    )
    _textio.write_text(
        root / "native" / "tests" / "test_wfm_bridge.c",
        TEST_C.format(**seams),
    )
    return root


def _replayed(root: Path) -> "tuple[dict[str, str], str]":
    """*root*'s replay -- the tree `apply` reads -- as its rename table and
    its composer seam header."""
    cfg = C.load(root)
    with tempfile.TemporaryDirectory() as tmp:
        _apply.replay_project(cfg, Path(tmp), root, prefix_checks=False)
        h = Path(tmp) / "native/inc/q/wfm_compose/wfm_compose_bridge.h"
        return _csym.renames(Path(tmp), cfg), h.read_text(encoding="utf-8")


def _renames(root: Path) -> "dict[str, str]":
    return _replayed(root)[0]


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    return _project(tmp_path_factory.mktemp("g1694"), SEAMS)


def test_the_bridge_header_declares_every_seam(project):
    """The premise: the replay DOES declare each name, which is what the
    rename table read -- a header without them would make every test below
    pass for the wrong reason."""
    h = _replayed(project)[1]
    for name in SEAMS.values():
        assert f"{name}(" in h, name


def test_apply_accepts_prefixed_seam_names(project):
    r = _ok("apply", cwd=project)
    assert "already declares" not in r.stdout + r.stderr


def test_upgrade_accepts_prefixed_seam_names(project):
    r = _ok("upgrade", cwd=project)
    assert "already declares" not in r.stdout + r.stderr


@pytest.mark.parametrize("key", sorted(SEAMS))
def test_no_seam_is_a_rename(project, key):
    names = _renames(project)
    seam = SEAMS[key]
    assert seam not in names.values(), (key, names.get(seam[len(P) + 1 :]))


def test_the_components_derived_names_still_rename(project):
    """The control: the exclusion did not empty the table."""
    names = _renames(project)
    for sym in ("create", "destroy", "state_t"):
        assert names.get(f"wfm_{sym}") == f"{P}_wfm_{sym}", sym


def test_a_seam_naming_a_derived_function_keeps_its_row(tmp_path):
    """Per header, not per name: a computed ``fn`` that names `wfm`'s own
    derived ``dp_wfm_step`` is still that derived name, declared by `wfm`'s
    header -- its row, and every check keyed by it, stays. ``step`` because
    only a header declares it: a lifecycle name would be re-found in the
    ``_core.c`` and hide an exclusion that went per name."""
    root = _project(tmp_path, {**SEAMS, "fn": f"{P}_wfm_step"})
    names = _renames(root)
    assert names.get("wfm_step") == f"{P}_wfm_step", names
    assert f"{P}_wfm_source_to_synth" not in names.values()
