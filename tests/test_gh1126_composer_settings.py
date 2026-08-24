"""gh-1126: a composer's post-construction settings.

A `kind = "composer"` had no way to declare a scalar the backing exposes
through a C setter/getter pair and sets once, after `create_fn` returns and
before the first `execute()`. So a composer's OBJECT face could not say what
its JSON face already could: doppler's scene JSON carries `seed_advance`,
jm's generated `from_json` honours it, and `Composer(...)` could not reach it
at all — a caller who wanted the setting had to hand-write the JSON.

It is deliberately not a `create_fn` argument. `create_fn` is the backing's
own C API, and widening its arity to carry a mode the backing chose to expose
as a setter would be jm dictating that API.

Three things had to line up, and rendering found two of them wrong before any
test did:

- the string→int lookup uses the **bare** enum convention (`_enum_index` /
    `_enum_<name>`) that composers emit, not a cname-prefixed one;
- `_enums_used` has to scan `settings`, or the emitted C references a table
    nothing declares — a compile error in the user's project.

Hence the compile gate. It follows `test_composer_codegen`'s convention of
building a narrow TU from the emitted fragment rather than the whole
`_ext.c`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _composer
from just_makeit import _config as C

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")

_SETTING = {
    "name": "seed_advance",
    "setter_fn": "wfm_compose_set_seed_advance",
    "getter_fn": "wfm_compose_seed_advance",
    "type": "int",
    "enum": "seed_advance",
}


def _cfg(settings=(_SETTING,), enum=True):
    cfg = {
        "project": {"name": "p"},
        "module": {
            "wc": {
                "kind": "composer",
                "backing": "wfm_compose",
                "header": "wfm/wfm.h",
                "composes": ["seg"],
                "segment": {
                    "struct": "wfm_seg_t",
                    "type_name": "Segment",
                    "fields": [{"name": "n", "type": "size_t"}],
                },
                "source": {
                    "object": "synth",
                    "struct": "wfm_src_t",
                    "type_name": "Synth",
                    "fields": [{"name": "fs", "type": "double"}],
                },
            }
        },
    }
    if enum:
        cfg["enum"] = [{"name": "seed_advance", "values": ["off", "noise"]}]
    if settings:
        cfg["module"]["wc"]["settings"] = list(settings)
    return cfg


class TestTheManifest:
    def test_the_rows_round_trip(self):
        """A key the dumper does not write is silently absent from anything
        that re-serialises the manifest — gh-1117's `process_global` reached
        every generator as False that way while its unit tests passed."""
        # tomllib is stdlib only on 3.11+; jm supports 3.9. A FUNCTION-level
        # bare import is a runtime failure rather than a collection error,
        # which is how it got past `test_stdlib_floor` -- that gate scanned
        # module level only (gh-1137's sibling lesson; the gate is widened).
        try:
            import tomllib
        except ModuleNotFoundError:  # Python < 3.11
            import tomli as tomllib

        dumped = C._dump(_cfg())
        assert C.composer_settings(tomllib.loads(dumped), "wc") == [_SETTING]

    def test_the_keys_are_recognised(self):
        from just_makeit import _keys

        assert [u.message() for u in _keys.unknown_keys(_cfg())] == []

    def test_a_typo_in_a_row_is_caught(self):
        from just_makeit import _keys

        cfg = _cfg()
        cfg["module"]["wc"]["settings"][0]["setter_function"] = "x"
        assert any(
            "setter_function" in u.message() for u in _keys.unknown_keys(cfg)
        )


class TestTheConstructor:
    def test_the_kwarg_is_popped_before_the_segments_split(self):
        """Left in `kw`, a setting would reach the segment constructor and be
        reported as "pass either segments or single-segment kwargs" — naming
        the wrong problem, which is gh-1126's secondary complaint."""
        ext = _composer.render_ext(_cfg(), "wc")
        pop = ext.index('PyDict_GetItemString(kw, "seed_advance")')
        split = ext.index("pass either segments or single-segment kwargs")
        assert pop < split

    def test_the_setter_runs_after_create(self):
        ext = _composer.render_ext(_cfg(), "wc")
        create = ext.index("self->state = wfm_compose_create(")
        apply_ = ext.index("wfm_compose_set_seed_advance(self->state,")
        assert create < apply_

    def test_the_value_is_a_plain_C_local(self):
        """Holding a PyObject* across segment building and create_fn would put
        a decref on every early return between them."""
        ext = _composer.render_ext(_cfg(), "wc")
        assert "int _st_seed_advance = 0;" in ext
        assert "int _st_seed_advance_set = 0;" in ext


class TestTheAttribute:
    def test_it_is_read_write(self):
        ext = _composer.render_ext(_cfg(), "wc")
        assert (
            '{"seed_advance", (getter)Composer_get_seed_advance,'
            " (setter)Composer_set_seed_advance, NULL, NULL}," in ext
        )

    def test_an_enum_setting_reads_back_as_its_name(self):
        """You must be able to assign what you just read."""
        ext = _composer.render_ext(_cfg(), "wc")
        assert "PyUnicode_FromString(_enum_seed_advance[_v])" in ext

    def test_the_stub_carries_both_faces(self):
        pyi = _composer.render_pyi(_cfg(), "wc")
        assert "    seed_advance: str" in pyi
        assert "seed_advance: str = ..." in pyi


class TestTheEnumWiring:
    def test_the_table_is_declared(self):
        """Without this the emitted C references a table nothing declares."""
        ext = _composer.render_ext(_cfg(), "wc")
        assert "_enum_seed_advance[] = {" in ext

    def test_the_bare_convention_is_used_consistently(self):
        """Composers emit `_enum_index` / `_enum_<name>`. A cname-prefixed
        lookup compiles against nothing."""
        ext = _composer.render_ext(_cfg(), "wc")
        assert "_enum_index(_enum_seed_advance, _s)" in ext
        assert "_enum_wc_seed_advance" not in ext


class TestZeroChurn:
    def test_a_composer_with_no_settings_is_unchanged(self):
        without = _composer.render_ext(_cfg(settings=()), "wc")
        assert "_st_" not in without
        assert "(setter)Composer_set_" not in without

    def test_the_stub_is_unchanged_too(self):
        pyi = _composer.render_pyi(_cfg(settings=()), "wc")
        assert "seed_advance" not in pyi


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_the_emitted_setting_code_compiles(tmp_path):
    """Compile the emitted enum table, getter and setter as one TU.

    Narrow by design — `test_composer_codegen` uses the same shape for the
    gh-343 include check, because building the whole `_ext.c` needs a backing
    whose structs match the fixture. What this proves is what rendering
    cannot: the table is declared before use, and the setter/getter calls
    typecheck against the backing's real signatures.
    """
    ext = _composer.render_ext(_cfg(), "wc")

    def _slice(start_marker, end_marker):
        i = ext.index(start_marker)
        return ext[i : ext.index(end_marker, i)]

    table = _slice("/* String-enum tables", "typedef struct")
    getter = _slice("Composer_get_seed_advance", "static PyGetSetDef")
    hdr = tmp_path / "wfm"
    hdr.mkdir()
    (hdr / "wfm.h").write_text(
        "#ifndef WFM_H\n#define WFM_H\n"
        "typedef struct wfm_compose_state wfm_compose_state_t;\n"
        "void wfm_compose_set_seed_advance(wfm_compose_state_t *s, int m);\n"
        "int wfm_compose_seed_advance(const wfm_compose_state_t *s);\n"
        "#endif\n"
    )
    src = tmp_path / "probe.c"
    src.write_text(
        "#define PY_SSIZE_T_CLEAN\n#include <Python.h>\n"
        "#include <string.h>\n"
        '#include "wfm/wfm.h"\n'
        "typedef struct { PyObject_HEAD wfm_compose_state_t *state;"
        " int destroyed; } ComposerObject;\n"
        f"{table}\n"
        f"static PyObject *\n{getter}\n"
    )
    r = subprocess.run(
        [
            _CC,
            "-c",
            "-std=c11",
            "-Werror=implicit-function-declaration",
            "-Werror=int-conversion",
            f"-I{tmp_path}",
            f"-I{__import__('sysconfig').get_paths()['include']}",
            str(src),
            "-o",
            str(tmp_path / "probe.o"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
