"""gh-998: a composer's straight-C seams are declared once, in a header.

A composer source hands two kinds of work back to the project as plain C:
``[module.X.source.generates] bridge_fn`` builds the composed generator from
the source struct, and each ``[[module.X.source.computed]] fn`` derives a
read-only property from it. jm knows both signatures exactly — the manifest
names the function and the rest is derived — and it wrote them down, but only
as ``extern`` lines *inside* the generated ``_ext.c``. No other translation
unit could see them.

The cost was a doppler test asserting that a composer's build path and the
standalone one agree. It could reach only the half with a public header, and
had to note in a comment that the other half is covered from Python instead —
a weaker claim than the one it wanted to make. The alternative was to
re-declare the extern in the test, which is the shape that drifts and is the
whole reason the generated declaration exists.

The compiler tier below is what makes this a real check rather than a string
match: it compiles a translation unit that includes **only** the generated
header and calls both functions. A test that merely greps for the prototype
would pass on a header that no consumer can actually use — a missing include,
an undeclared struct, a stray semicolon.

Both seams are covered, not just the `bridge_fn` the issue names. They are the
same defect emitted twenty lines apart, and fixing the reported half alone is
the mistake gh-994 already made once.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _composer  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

_MODULE = "playlist"

#: A composer with BOTH seams, and deliberately not doppler's: the primitive
#: is generic, and a fixture named after the one consumer that drove it is how
#: a generic feature quietly acquires a domain assumption.
_CFG = {
    "project": {"name": "studio", "version": "0.1.0"},
    "module": {
        _MODULE: {
            "kind": "composer",
            "backing": "playlist",
            "composes": ["clip"],
            "source": {
                "object": "clip",
                "struct": "clip_t",
                "type_name": "Clip",
                "fields": [
                    {"name": "gain", "type": "double", "default": "1.0"}
                ],
                "generates": {
                    "generator": "clip",
                    "bridge_fn": "clip_from_source",
                },
                "computed": [
                    {
                        "name": "duration",
                        "type": "double",
                        "fn": "clip_duration",
                    },
                    {
                        "name": "n_frames",
                        "type": "size_t",
                        "fn": "clip_n_frames",
                    },
                ],
            },
            "segment": {
                "type_name": "Track",
                "struct": "track_t",
                "sources": "multi",
                "fields": [{"name": "dur", "type": "size_t"}],
            },
            "oo": {"composer_type_name": "Mix"},
        }
    },
}


def _bare_cfg() -> dict:
    """The same composer with neither seam declared."""
    import copy

    cfg = copy.deepcopy(_CFG)
    del cfg["module"][_MODULE]["source"]["generates"]
    del cfg["module"][_MODULE]["source"]["computed"]
    return cfg


class TestOneDeclarationPerSeam:
    def test_header_declares_the_bridge_and_every_computed_fn(self):
        h = _composer.render_bridge_h(_CFG, _MODULE)
        assert "clip_state_t *clip_from_source(const clip_t *, double);" in h
        assert "double clip_duration(const clip_t *);" in h
        assert "size_t clip_n_frames(const clip_t *);" in h

    def test_the_binding_includes_it_and_declares_nothing(self):
        """Both halves. Either alone passes on a declaration that vanished."""
        ext = _composer.render_ext(_CFG, _MODULE)
        assert f'#include "{_MODULE}/{_MODULE}_bridge.h"' in ext
        assert "extern " not in ext

    def test_no_header_when_the_source_declares_no_seam(self):
        """The file exists only where it has something to say.

        The control for every assertion above: without it, a `render_bridge_h`
        that returned a constant would satisfy them all.
        """
        assert _composer.render_bridge_h(_bare_cfg(), _MODULE) == ""
        ext = _composer.render_ext(_bare_cfg(), _MODULE)
        assert "_bridge.h" not in ext

    def test_header_is_self_contained(self):
        """It pulls in the struct and the generator state itself.

        A header whose consumer has to know which two headers to include
        first is one they will get wrong once, and the point of publishing it
        is that nobody has to reconstruct anything.
        """
        h = _composer.render_bridge_h(_CFG, _MODULE)
        assert '#include "playlist/playlist_core.h"' in h
        assert '#include "clip/clip_core.h"' in h

    def test_include_guard_is_namespaced_by_the_module(self):
        h = _composer.render_bridge_h(_CFG, _MODULE)
        assert "#ifndef PLAYLIST_BRIDGE_H" in h
        assert "#define PLAYLIST_BRIDGE_H" in h


@_needs_cc
class TestAConsumerCanActuallyUseIt:
    """The claim is *usable by another translation unit*, so compile one.

    Syntax-only, with hand-written stand-ins for the two headers the generated
    one includes — those are the project's, not jm's, and inventing them here
    is exactly what a real consumer's tree already has.
    """

    def _tree(self, tmp_path: Path) -> Path:
        inc = tmp_path / "native" / "inc"
        (inc / "playlist").mkdir(parents=True)
        (inc / "clip").mkdir(parents=True)
        (inc / "playlist" / "playlist_core.h").write_text(
            "#ifndef PLAYLIST_CORE_H\n#define PLAYLIST_CORE_H\n"
            "#include <stddef.h>\n"
            "typedef struct { double gain; } clip_t;\n"
            "#endif\n",
            encoding="utf-8",
        )
        (inc / "clip" / "clip_core.h").write_text(
            "#ifndef CLIP_CORE_H\n#define CLIP_CORE_H\n"
            "typedef struct clip_state clip_state_t;\n"
            "#endif\n",
            encoding="utf-8",
        )
        (inc / "playlist" / "playlist_bridge.h").write_text(
            _composer.render_bridge_h(_CFG, _MODULE), encoding="utf-8"
        )
        return inc

    def _compile(self, tmp_path, body: str):
        inc = self._tree(tmp_path)
        src = tmp_path / "consumer.c"
        src.write_text(body, encoding="utf-8")
        return subprocess.run(
            [
                _CC,
                "-fsyntax-only",
                "-std=gnu99",
                "-Wall",
                "-Werror",
                f"-I{inc}",
                str(src),
            ],
            capture_output=True,
            text=True,
        )

    def test_a_c_consumer_compiles_against_the_header_alone(self, tmp_path):
        """One include, and every seam is callable. `-Werror` is the check.

        Without it an implicit declaration is a warning, and the test would
        pass on a header that declared nothing at all — which is the exact
        state this issue was filed about.
        """
        proc = self._compile(
            tmp_path,
            (
                '#include "playlist/playlist_bridge.h"\n'
                "int probe(const clip_t *c);\n"
                "int probe(const clip_t *c)\n"
                "{\n"
                "    clip_state_t *g = clip_from_source(c, 48000.0);\n"
                "    double d = clip_duration(c);\n"
                "    size_t n = clip_n_frames(c);\n"
                "    return (g != 0) + (d > 0.0) + (int)n;\n"
                "}\n"
            ),
        )
        assert proc.returncode == 0, proc.stderr

    def test_the_header_is_include_guarded_against_double_inclusion(
        self, tmp_path
    ):
        proc = self._compile(
            tmp_path,
            (
                '#include "playlist/playlist_bridge.h"\n'
                '#include "playlist/playlist_bridge.h"\n'
                "int probe(void);\n"
                "int probe(void) { return 0; }\n"
            ),
        )
        assert proc.returncode == 0, proc.stderr


class TestItReachesTheProjectOnDisk:
    """Rendering the header is not shipping it.

    Every assertion above reads `render_bridge_h`'s return value, and all of
    them pass just as well on a header that is never written and never copied
    down — which is a *worse* version of the bug this closes, since the
    prototype would then exist only in jm's imagination. codecov caught the
    gap: `materialize`'s write and `_apply`'s glue entry were the two lines
    this file did not reach.
    """

    def _project(self, root: Path) -> Path:
        from just_makeit import _config as C
        from just_makeit._new import run as new_run

        new_run("proj", root, ["clip"], [("gain", "double", "1.0")])
        cfg = C.load(root)
        mod = dict(_CFG["module"][_MODULE])
        mod["package"] = "audio"
        cfg.setdefault("module", {})[_MODULE] = mod
        C.save(root, cfg)
        return root

    def test_materialize_writes_the_header(self, tmp_path):
        from just_makeit import _config as C

        root = self._project(tmp_path / "proj")
        _composer.materialize(C.load(root), root, _MODULE)
        h = root / "native" / "inc" / _MODULE / f"{_MODULE}_bridge.h"
        assert h.exists(), "the bridge header was rendered but never written"
        assert "clip_from_source" in h.read_text(encoding="utf-8")

    def test_materialize_writes_no_header_without_a_seam(self, tmp_path):
        """The control. Without it, an unconditional write passes above."""
        from just_makeit import _config as C
        from just_makeit._new import run as new_run

        root = tmp_path / "proj"
        new_run("proj", root, ["clip"], [("gain", "double", "1.0")])
        cfg = C.load(root)
        mod = dict(_bare_cfg()["module"][_MODULE])
        mod["package"] = "audio"
        cfg.setdefault("module", {})[_MODULE] = mod
        C.save(root, cfg)
        _composer.materialize(C.load(root), root, _MODULE)
        assert not (
            root / "native" / "inc" / _MODULE / f"{_MODULE}_bridge.h"
        ).exists()

    def test_apply_carries_it_into_the_real_tree(self, tmp_path):
        """`apply` materializes into a TEMP tree and copies a listed subset.

        A file written there but missing from `_apply`'s glue list reaches the
        project never — the gh-942 shape, and the reason that list is gated on
        the same predicate rather than hand-maintained beside it.
        """
        from just_makeit._apply import run as apply_run

        root = self._project(tmp_path / "proj")
        apply_run(root)
        h = root / "native" / "inc" / _MODULE / f"{_MODULE}_bridge.h"
        assert h.exists(), "apply left the bridge header in the temp tree"
        text = h.read_text(encoding="utf-8")
        assert "clip_from_source" in text
        assert "clip_duration" in text
        assert "clip_n_frames" in text

    def test_apply_refreshes_a_stale_header(self, tmp_path):
        """The claim `_apply`'s glue entry actually makes.

        A first `apply` writes the header through the missing-files path, so
        asserting only that it appears passes with the glue entry deleted —
        measured, and the reason this test exists beside the one above. What
        the entry buys is *regeneration*: the header is derived, so a stale or
        hand-edited copy must be overwritten from the manifest on the next
        apply, exactly like the `_ext.c` and the `.pyi` beside it.
        """
        from just_makeit._apply import run as apply_run

        root = self._project(tmp_path / "proj")
        apply_run(root)
        h = root / "native" / "inc" / _MODULE / f"{_MODULE}_bridge.h"
        h.write_text("/* stale */\n", encoding="utf-8")
        apply_run(root)
        text = h.read_text(encoding="utf-8")
        assert "/* stale */" not in text, (
            "a derived header survived an apply — it is not in the glue list"
        )
        assert "clip_from_source" in text
