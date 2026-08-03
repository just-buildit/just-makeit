"""gh-724: a `///<` on a struct in an *included* header reaches derivation.

Reported as "a `result_fields` entry's doc can't fall back to the C result
struct's `///<` — no manifest→struct mapping", with the ask being an explicit
`result_struct = "tone_meas_t"` key or inference from the method's C return
type.

Reproducing first showed the symptom was real and the mechanism was not. The
manifest→struct mapping is not missing: `member_doc` looks a field up by **bare
name**, so with the struct in the component's own `<obj>_core.h` the `///<`
fallback already worked, on both faces, for both spellings. What actually
blocked it is that `_load_doc_blocks` reads exactly one file, and doppler's
`tonemeas_core.h` *includes* `measure/measure_core.h`, where `tone_meas_t` is
declared.

That distinction matters for more than tidiness: the proposed fix would not
have worked. Knowing the struct is named `tone_meas_t` still leaves jm without
the *file* it is declared in, so the name→file gap would have remained after
building the name→struct mapping.

The fix follows the sacred header's own project-local `#include`s, transitively
and for member docs only — so this is not specific to records. Any surface
gh-671 feeds (properties included) can now be documented on a struct that lives
in a shared header.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import (  # noqa: E402
    _load_doc_blocks,
    run as object_run,
)


def _quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _write_header(root: Path, sub: str, body: str) -> None:
    d = root / "native" / "inc" / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sub}_core.h").write_text(body, encoding="utf-8")


def _include(root: Path, obj: str, rel: str) -> None:
    h = root / "native" / "inc" / obj / f"{obj}_core.h"
    t = h.read_text(encoding="utf-8")
    h.write_text(
        t.replace(
            '#include "clib_common.h"',
            f'#include "clib_common.h"\n#include "{rel}"',
            1,
        ),
        encoding="utf-8",
    )


def _record_project(tmp_path: Path) -> Path:
    """doppler's shape: the record struct lives in a shared header."""
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(
        object_run,
        root,
        "tm",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    _quiet(
        method_run,
        root,
        "tm",
        "analyze",
        None,
        "float[]",
        "tone_meas_t",
        False,
        [],
        result_fields=[
            {"name": "snr", "type": "double"},
            {"name": "enob", "type": "double"},
        ],
        single=True,
        record_name="ToneMetrics",
    )
    # measure -> psd, so the second hop is exercised too.
    _write_header(
        root,
        "measure",
        '#include "psd/psd_core.h"\n'
        "typedef struct {\n"
        "    double snr;  ///< SENTINEL_SNR Signal-to-noise ratio, dB.\n"
        "} tone_meas_t;\n",
    )
    _write_header(
        root,
        "psd",
        "typedef struct {\n"
        "    double enob;  /**< SENTINEL_ENOB Effective number of bits. */\n"
        "} psd_t;\n",
    )
    _include(root, "tm", "measure/measure_core.h")
    _quiet(apply_run, root)
    return root


def _faces(root: Path) -> tuple[str, str]:
    return (
        (root / "src/demo/tm.pyi").read_text(encoding="utf-8"),
        (root / "native/src/tm/tm_ext.c").read_text(encoding="utf-8"),
    )


class TestIncludedHeaders:
    def test_one_level_reaches_both_faces(self, tmp_path):
        pyi, ext = _faces(_record_project(tmp_path))
        missing = [
            face
            for face, blob in (("python", pyi), ("runtime", ext))
            if "SENTINEL_SNR" not in blob
        ]
        assert not missing, f"missing from: {', '.join(missing)}"

    def test_the_second_hop_reaches_both_faces(self, tmp_path):
        """A shared header that itself includes the one holding the struct."""
        pyi, ext = _faces(_record_project(tmp_path))
        missing = [
            face
            for face, blob in (("python", pyi), ("runtime", ext))
            if "SENTINEL_ENOB" not in blob
        ]
        assert not missing, f"missing from: {', '.join(missing)}"

    def test_no_manifest_doc_was_needed(self, tmp_path):
        """The point of the issue: the text exists only in C."""
        root = _record_project(tmp_path)
        manifest = (root / "just-makeit.toml").read_text(encoding="utf-8")
        assert "SENTINEL_SNR" not in manifest
        assert "SENTINEL_ENOB" not in manifest


class TestScoping:
    def test_the_components_own_header_wins(self, tmp_path):
        """Nearest-declaration-wins, the same rule the rest of derivation uses."""
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "tm",
            None,
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        _write_header(
            root,
            "shared",
            "typedef struct { double snr; ///< FROM_INCLUDE\n} a_t;\n",
        )
        _include(root, "tm", "shared/shared_core.h")
        h = root / "native/inc/tm/tm_core.h"
        h.write_text(
            h.read_text(encoding="utf-8")
            + "\ntypedef struct { double snr; ///< FROM_OWN\n} b_t;\n",
            encoding="utf-8",
        )
        blocks = _load_doc_blocks(root, "tm")
        assert blocks["<member>snr"].brief == "FROM_OWN"

    def test_system_includes_are_not_followed(self, tmp_path):
        """`<...>` is a system header and cannot document this project."""
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "tm",
            None,
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        h = root / "native/inc/tm/tm_core.h"
        h.write_text(
            h.read_text(encoding="utf-8").replace(
                '#include "clib_common.h"',
                '#include "clib_common.h"\n#include <stdio.h>',
                1,
            ),
            encoding="utf-8",
        )
        # The assertion is that this returns rather than raising or hanging.
        assert isinstance(_load_doc_blocks(root, "tm"), dict)

    def test_an_include_cycle_terminates(self, tmp_path):
        """Include guards make cycles the normal case, not an edge case."""
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "tm",
            None,
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        _write_header(
            root,
            "a",
            '#include "b/b_core.h"\n'
            "typedef struct { double x; ///< FROM_A\n} a_t;\n",
        )
        _write_header(
            root,
            "b",
            '#include "a/a_core.h"\ntypedef struct '
            "{ double y; ///< FROM_B\n} b_t;\n",
        )
        _include(root, "tm", "a/a_core.h")
        blocks = _load_doc_blocks(root, "tm")
        assert blocks["<member>x"].brief == "FROM_A"
        assert blocks["<member>y"].brief == "FROM_B"

    def test_a_missing_include_is_ignored(self, tmp_path):
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "tm",
            None,
            state_vars=[("gain", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        _include(root, "tm", "nope/nope_core.h")
        assert isinstance(_load_doc_blocks(root, "tm"), dict)


def test_a_rewritten_header_is_not_served_from_cache(tmp_path):
    """The memoization is keyed on stat, so an edit mid-process is seen."""
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(
        object_run,
        root,
        "tm",
        None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    _write_header(
        root, "shared", "typedef struct { double snr; ///< FIRST\n} a_t;\n"
    )
    _include(root, "tm", "shared/shared_core.h")
    assert _load_doc_blocks(root, "tm")["<member>snr"].brief == "FIRST"

    p = root / "native/inc/shared/shared_core.h"
    p.write_text(
        "typedef struct { double snr; ///< SECOND\n} a_t;\n"
        # Change the size too: a same-size same-mtime_ns rewrite is not
        # something a human edit produces, and asserting on it would be
        # asserting on the filesystem's clock rather than on jm.
        "/* padding to change the size */\n",
        encoding="utf-8",
    )
    assert _load_doc_blocks(root, "tm")["<member>snr"].brief == "SECOND"
