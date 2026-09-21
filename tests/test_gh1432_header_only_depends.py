"""gh-1432: a header-only component could not declare a dependency at all.

Found doing the real ring swap in doppler on 0.82.0. gh-1311 made a
header-only core an **INTERFACE** library; every `target_link_libraries` /
`target_include_directories` beside it kept saying `PUBLIC`, and for an
INTERFACE library `INTERFACE` is the only legal keyword -- CMake refuses the
target outright:

    CMake Error: INTERFACE library can only be used with the INTERFACE
    keyword of target_link_libraries

`PUBLIC` was also reaching for what `INTERFACE` says: an INTERFACE library
has no build of its own, so "mine and my consumers'" and "my consumers'" are
the same statement.

The consequence is bigger than a build error. doppler's ring is header-only
and its `wait()` reads an interrupt flag declared `process_global` -- so
`depends_on` is not only a link line, it is what makes jm generate the
cross-module rendezvous. Without it the module links its own copy of the flag
and Ctrl-C never reaches a blocked `wait()`.

**The keyword and the library kind are one decision**, so they are now
decided in one place (`_render.core_scope` beside `component_core_decl`) and
emitted by one function with five callers.

The second half is the reason the first was hard to see: at OBJECT-CREATION
the component is not in the manifest yet, so `C.is_header_only(cfg, comp)`
answers False and the line reverts to `PUBLIC` -- while the core declaration
beside it, reading the in-scope argument, correctly says INTERFACE. One file,
two answers to one question. The same trap `_param_headers_at_create` exists
for.

GATE: a manifest key a method shape accepts is honoured in the generated
      binding, its stub, and a replayed script -- or it is refused.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import run_cli

CML = Path("native") / "src" / "ring" / "CMakeLists.txt"


def _project(tmp_path: Path, *, header_only: bool) -> Path:
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    assert run_cli("object", "dep", "--module", "m", cwd=proj).returncode == 0
    args = [
        "object",
        "ring",
        "--module",
        "m",
        "--no-state",
        "--no-step",
        "--init-param",
        "n:size_t:16",
    ]
    if header_only:
        args.append("--header-only")
    r = run_cli(*args, cwd=proj)
    assert r.returncode == 0, r.stderr
    return proj


def _declare_dep(proj: Path) -> None:
    """Add `depends_on` to the object, the way the issue's repro does."""
    frag = proj / "objects" / "ring.toml"
    s = frag.read_text()
    frag.write_text(
        s.replace(
            "[[ring.init_params]]",
            '[[ring.depends_on]]\nname = "dep"\nlink = true\n\n'
            "[[ring.init_params]]",
            1,
        )
    )


class TestTheKeywordFollowsTheLibraryKind:
    def test_a_header_only_core_links_with_interface(self, tmp_path):
        proj = _project(tmp_path, header_only=True)
        _declare_dep(proj)
        assert run_cli("apply", cwd=proj).returncode == 0

        cml = (proj / CML).read_text()
        assert "add_library(ring_core INTERFACE)" in cml
        # The line CMake refuses. Anchored on the target, because the file
        # has several `PUBLIC` link lines for other targets.
        assert "target_link_libraries(ring_core INTERFACE" in cml
        assert "target_link_libraries(ring_core PUBLIC" not in cml

    def test_a_compiled_core_still_links_with_public(self, tmp_path):
        """The fix must not flip the ordinary case."""
        proj = _project(tmp_path, header_only=False)
        _declare_dep(proj)
        assert run_cli("apply", cwd=proj).returncode == 0

        cml = (proj / CML).read_text()
        assert "add_library(ring_core OBJECT" in cml
        assert "target_link_libraries(ring_core PUBLIC" in cml

    def test_the_declaration_and_its_link_line_agree(self, tmp_path):
        """The two halves are asserted together.

        Apart, each looks right on its own: the core declaration read the
        in-scope flag and said INTERFACE while the link line read the
        manifest -- which does not have the component yet at creation --
        and said PUBLIC. A file can only be wrong in the relationship.
        """
        proj = _project(tmp_path, header_only=True)
        _declare_dep(proj)
        assert run_cli("apply", cwd=proj).returncode == 0

        cml = (proj / CML).read_text()
        interface_lib = "add_library(ring_core INTERFACE)" in cml
        scope = "INTERFACE" if interface_lib else "PUBLIC"
        for line in cml.splitlines():
            # `(ring_core ` exactly -- `test_ring_core` and `bench_ring_core`
            # are different targets whose own scopes are none of this test's
            # business, and a prefix match swept them in.
            if "(ring_core " in line and line.startswith("target_"):
                assert scope in line, line


class TestAModuleLevelDependsOnIsReported:
    def test_an_object_module_says_the_key_is_dropped(self, tmp_path):
        """Accepted, exit 0, and nothing generated -- the gh-1418 shape.

        Reported rather than given a key vocabulary: an object module has
        no stated key set, and inventing one to hold a single finding is
        how a channel starts warning on valid keys.
        """
        from just_makeit import _keys

        cfg = {
            "module": {
                "m": {
                    "objects": ["a"],
                    "depends_on": [{"name": "ext", "link": True}],
                }
            }
        }
        msgs = [u.message() for u in _keys.unknown_keys(cfg)]
        assert any("depends_on" in m for m in msgs), msgs
        # It names the spelling that works, and why it matters.
        joined = "\n".join(msgs)
        assert "[[<obj>.depends_on]]" in joined
        assert "process_global" in joined

    def test_a_kind_module_keeps_it_silently(self, tmp_path):
        """It is a real key there -- doppler's `sample_clock` uses it."""
        from just_makeit import _keys

        for kind in ("handle", "capsule", "composer"):
            cfg = {
                "module": {"h": {"kind": kind, "depends_on": [{"name": "x"}]}}
            }
            msgs = [u.message() for u in _keys.unknown_keys(cfg)]
            assert not any("depends_on" in m for m in msgs), (kind, msgs)
