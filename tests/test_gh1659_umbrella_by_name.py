"""gh-1659: `_createonly` claims the umbrella header by its name, not by a
glob that also claims every author header beside it.

The umbrella rule was ``native/inc/*.h``, and fnmatch's ``*`` crosses ``/``:
it classified EVERY header under ``native/inc/`` as jm's rewritten umbrella
-- ``native/inc/helper.h`` in the flat layout, ``native/inc/<pkg>/helper.h``
in the prefixed one, doppler's ``dp_syncword.h``. Anything trusting
`classify` then skipped them. gh-1591's `unrenamed` refusal reads
`_csym._author_files`, which asks `classify`, so under a new ``c_prefix`` an
author header calling ``lo_create`` was never named -- while the same helper
under ``native/src/lo/`` was.

The class was the grammar, not one pattern: every ``*`` crossed ``/``. Now
``*`` stays in one path segment and ``**`` crosses where a rule means it,
and a header's rule matches `_incpath.layout_free`'s name for it, so the
umbrella is ``inc:{pkg}.h`` in both layouts with no second spelling of
either. A nested author source such as ``native/src/lo/vendor/v_ext.c`` was
the same over-match against ``native/src/*/*_ext.c``, and is checked here
beside the header.

Every fixture is built by running jm, in both header layouts.

GATE: in a flat (pre-`PREFIXED_SCHEMA`) and a prefixed project built by jm,
      `classify` returns None for an author header at ``native/inc/`` and at
      ``native/inc/<pkg>/`` and for a nested author ``_ext.c``, the umbrella
      is still RECONCILED and ``clib_common.h`` still JM; and with
      ``c_prefix = "zz"`` `apply` exits 1 naming ``helper.h``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _createonly as CO
from just_makeit import _incpath as INC
from just_makeit import _textio
from just_makeit._new import run as new_run

PKG = "q"

#: Both header layouts: the flat one before `PREFIXED_SCHEMA`, and from it.
LAYOUTS = {
    "flat": INC.PREFIXED_SCHEMA - 1,
    "prefixed": INC.PREFIXED_SCHEMA,
}

#: An author's C that calls a name `c_prefix` renames, and so must be named
#: by `apply`'s refusal. It says nothing else jm could own.
CALLER = (
    "static inline void *\nq_helper (void)\n{\n  return lo_create ();\n}\n"
)


@pytest.fixture(scope="module", params=sorted(LAYOUTS))
def project(request, tmp_path_factory) -> Path:
    """A project with one object, ``lo``, scaffolded by jm in one layout."""
    root = tmp_path_factory.mktemp(f"g1659-{request.param}") / PKG
    new_run(PKG, root, c_prefix=None, schema=LAYOUTS[request.param])
    r = run_cli("object", "lo", "--no-state", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert INC.prefixed(root) == (request.param == "prefixed")
    return root


#: An author header beside the umbrella in each layout: at the ``-I`` root,
#: and under the package directory. Both asked of both projects.
AUTHOR_HEADERS = (f"{INC.INC_DIR}/helper.h", f"{INC.INC_DIR}/{PKG}/helper.h")


@pytest.mark.parametrize("rel", AUTHOR_HEADERS)
def test_an_author_header_is_unclassified(project, rel):
    assert CO.classify(rel, project) is None


def test_a_nested_author_source_is_unclassified(project):
    rel = "native/src/lo/vendor/v_ext.c"
    assert CO.classify(rel, project) is None


def test_the_umbrella_is_still_reconciled(project):
    rel = INC.rel(f"{PKG}.h", project)
    assert (project / rel).is_file(), f"jm wrote no umbrella at {rel}"
    rule = CO.classify(rel, project)
    assert rule is not None and rule.kind == CO.RECONCILED, rule


def test_jms_own_headers_are_still_jms(project):
    rel = INC.rel("clib_common.h", project)
    assert (project / rel).is_file(), f"jm wrote no {rel}"
    rule = CO.classify(rel, project)
    assert rule is not None and rule.kind == CO.JM, rule


def test_apply_names_an_author_header_under_a_new_prefix(project, tmp_path):
    import shutil

    root = tmp_path / PKG
    shutil.copytree(project, root)
    header = INC.path(root, "helper.h")
    nested = root / "native/src/lo/vendor/v_ext.c"
    nested.parent.mkdir(parents=True)
    for p in (header, nested):
        _textio.write_text(p, CALLER)
    toml = root / C.FILENAME
    text = toml.read_text(encoding="utf-8")
    _textio.write_text(
        toml, text.replace("[project]\n", '[project]\nc_prefix = "zz"\n', 1)
    )
    r = run_cli("apply", cwd=root)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    for p in (header, nested):
        rel = p.relative_to(root).as_posix()
        assert f"{rel} still spells the unprefixed `lo_create`" in out, (
            rel,
            out,
        )
