"""gh-999: declare a repeated field group once, instantiate it under a prefix.

jm's type vocabulary has no struct in either direction, so a C descriptor built
from N repeats of the same small field group had to be flattened into one long
name-prefixed constructor list, written out once per repeat. doppler's
`wfm_frame_t` is three instances of one 11-field group: ~34 hand-written
`init_params`, with every doc, default and enum binding written three times —
and the three copies free to drift while `jm status --check` stays clean,
because they are three unrelated params as far as jm is concerned.

    [[group]]
    name = "wfm_seq"
    [[group.fields]]
    name = "kind"
    type = "int"

    [[frame.init_groups]]
    group  = "wfm_seq"
    prefix = "preamble"      # -> preamble_kind, …

**This is not jm learning structs**, and the issue is explicit about not asking
for that. The expansion produces *exactly* what the hand-written list produces,
so every downstream face — the C prototype, the kwlist, the `.pyi`, the
docstring — needs no change. `TestItIsExactlyTheHandWrittenList` asserts that
directly, by rendering both and comparing, rather than by checking the shapes
jm happens to emit today.

**Where the expansion lives is the load-bearing decision.** It runs in
`_config.load`, the one place every reader passes through — not behind
`C.init_params`. Ten sites read `.get("init_params")` raw (the glue, the stubs,
apply's replay, the handle generator, `jm bind`), so an expansion behind one
accessor would reach some and not others, which is the half-wired-feature shape
this repo keeps finding. `TestEveryReaderSeesTheExpansion` is the gate, and it
scans for those readers rather than naming them.

**The declaration round-trips, not the expansion.** A grouped param is marked,
and the writer skips it — otherwise the first `jm apply` would silently flatten
the group back into the 34 params it exists to remove, and the feature would
delete itself on first use.
"""

from __future__ import annotations

import ast
import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _apply  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

GROUP_TOML = """
[[group]]
name = "wfm_seq"
[[group.fields]]
name = "kind"
type = "int"
default = "0"
doc = "Which sequence family this leg uses."
[[group.fields]]
name = "len"
type = "size_t"
default = "16"
"""

#: The same two fields written out by hand, twice, with the prefixes a group
#: instantiation would have produced. The comparison target.
HAND_WRITTEN = """
[[frame.init_params]]
name = "preamble_kind"
type = "int"
default = "0"
doc = "Which sequence family this leg uses."

[[frame.init_params]]
name = "preamble_len"
type = "size_t"
default = "16"

[[frame.init_params]]
name = "sync_kind"
type = "int"
default = "0"
doc = "Which sequence family this leg uses."

[[frame.init_params]]
name = "sync_len"
type = "size_t"
default = "16"
"""

GROUPED = """
[[frame.init_groups]]
group = "wfm_seq"
prefix = "preamble"

[[frame.init_groups]]
group = "wfm_seq"
prefix = "sync"
"""


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _project(tmp_path: Path, name: str, extra: str, group: bool) -> Path:
    root = tmp_path / name
    _quiet(new_run, name, root)
    _quiet(
        object_run,
        root,
        "frame",
        module=None,
        arg_type="float",
        return_type="float",
    )
    if group:
        m = root / "just-makeit.toml"
        m.write_text(m.read_text(encoding="utf-8") + GROUP_TOML, "utf-8")
    frag = root / "objects" / "frame.toml"
    target = frag if frag.is_file() else root / "just-makeit.toml"
    target.write_text(target.read_text(encoding="utf-8") + extra, "utf-8")
    return root


@pytest.fixture()
def grouped(tmp_path):
    return _project(tmp_path, "g", GROUPED, group=True)


@pytest.fixture()
def by_hand(tmp_path):
    return _project(tmp_path, "h", HAND_WRITTEN, group=False)


class TestTheExpansion:
    def test_one_row_becomes_the_group_s_fields_under_its_prefix(
        self, grouped
    ):
        cfg = C.load(grouped)
        assert [p["name"] for p in cfg["frame"]["init_params"]] == [
            "preamble_kind",
            "preamble_len",
            "sync_kind",
            "sync_len",
        ]

    def test_field_properties_travel(self, grouped):
        """Default, type and doc are declared ONCE and reach every repeat.

        That is the whole point: three copies of a doc string are three
        chances for two of them to be right.
        """
        (kind,) = [
            p
            for p in C.load(grouped)["frame"]["init_params"]
            if p["name"] == "sync_kind"
        ]
        assert kind["type"] == "int"
        assert kind["default"] == "0"
        assert kind["doc"] == "Which sequence family this leg uses."

    def test_two_instantiations_cannot_drift(self, grouped):
        """The property a hand-written list cannot have.

        Everything but the name is the same object's declaration, so there is
        no edit that changes one repeat and not the other.
        """
        params = {
            p["name"]: p for p in C.load(grouped)["frame"]["init_params"]
        }
        for key in ("type", "default", "doc"):
            assert params["preamble_kind"].get(key) == params["sync_kind"].get(
                key
            )

    def test_an_unprefixed_row_uses_the_bare_field_names(self, tmp_path):
        root = _project(
            tmp_path,
            "np",
            '\n[[frame.init_groups]]\ngroup = "wfm_seq"\n',
            group=True,
        )
        assert [p["name"] for p in C.load(root)["frame"]["init_params"]] == [
            "kind",
            "len",
        ]

    def test_hand_written_params_keep_their_place(self, tmp_path):
        """Groups instantiate AFTER the explicit list — the order the
        manifest reads in, so the two can coexist predictably."""
        root = _project(
            tmp_path,
            "hw",
            '\n[[frame.init_params]]\nname = "crc"\ntype = "int"\n' + GROUPED,
            group=True,
        )
        names = [p["name"] for p in C.load(root)["frame"]["init_params"]]
        assert names[0] == "crc"
        assert names[1:] == [
            "preamble_kind",
            "preamble_len",
            "sync_kind",
            "sync_len",
        ]

    def test_an_unknown_group_is_left_alone(self, tmp_path):
        """A typo must not be a traceback out of every command at once.

        `_keys` reports an unrecognised declaration in the voice the author
        already knows; raising here would replace that with a stack trace
        from `jm status`, `jm apply` and `jm script` alike.
        """
        root = _project(
            tmp_path,
            "unk",
            '\n[[frame.init_groups]]\ngroup = "nope"\nprefix = "x"\n',
            group=True,
        )
        assert C.load(root)["frame"].get("init_params", []) == []


class TestItIsExactlyTheHandWrittenList:
    """The claim the issue makes, asserted rather than assumed.

    "The generated binding would be exactly what a hand-written list produces
    today." Two projects, one grouped and one hand-written, compared on the
    artefacts a user sees.
    """

    def _render(self, root: Path) -> tuple[str, str]:
        _quiet(_apply.run, root)
        pkg = C.project_name(C.load(root))
        header = (
            root / "native" / "inc" / "frame" / "frame_core.h"
        ).read_text(encoding="utf-8")
        pyi = (root / "src" / pkg / "frame.pyi").read_text(encoding="utf-8")
        return header, pyi

    def test_the_c_prototype_is_identical(self, grouped, by_hand):
        gh, _ = self._render(grouped)
        hh, _ = self._render(by_hand)

        def proto(text):
            return next(
                ln for ln in text.splitlines() if "frame_create(" in ln
            )

        assert proto(gh) == proto(hh)

    def test_the_stub_constructor_is_identical(self, grouped, by_hand):
        _, gp = self._render(grouped)
        _, hp = self._render(by_hand)

        def init(text):
            i = text.index("def __init__")
            return text[i : text.index("None: ...", i)]

        assert init(gp) == init(hp)

    def test_the_tuple_form_is_identical(self, grouped, by_hand):
        """What `make_state_ctx` actually consumes.

        Comparing the rendered files catches a difference; comparing the
        tuples says the two declarations are the same input, which is the
        stronger claim and the one that survives a future render change.
        """
        assert C.init_params(C.load(grouped), "frame") == C.init_params(
            C.load(by_hand), "frame"
        )


class TestTheDeclarationRoundTrips:
    """The expansion must never be written back.

    A writer that persisted it would flatten the group into the long list on
    the first `jm apply` — the feature deleting itself on first use, silently,
    with `jm status --check` reporting clean either way.
    """

    def _manifest_text(self, root: Path) -> str:
        return "\n".join(
            p.read_text(encoding="utf-8")
            for p in [root / "just-makeit.toml", *root.glob("objects/*.toml")]
            if p.is_file()
        )

    def test_apply_keeps_the_group_rows(self, grouped):
        _quiet(_apply.run, grouped)
        text = self._manifest_text(grouped)
        assert text.count("[[frame.init_groups]]") == 2
        assert "[[group]]" in text

    def test_apply_writes_no_expanded_params(self, grouped):
        _quiet(_apply.run, grouped)
        text = self._manifest_text(grouped)
        assert "[[frame.init_params]]" not in text
        assert "preamble_kind" not in text

    def test_apply_is_idempotent(self, grouped):
        _quiet(_apply.run, grouped)
        before = self._manifest_text(grouped)
        _quiet(_apply.run, grouped)
        assert self._manifest_text(grouped) == before

    def test_status_check_is_clean(self, grouped):
        _quiet(_apply.run, grouped)
        assert _status.run(grouped, check=True) == 0

    def test_a_mutating_command_does_not_flatten_the_group(self, grouped):
        """The case `jm apply` alone could not see, and the real bug.

        `apply` on an unchanged tree may rewrite nothing, so every assertion
        above passed with the writer guard deleted. A command that actually
        mutates the manifest goes through the tomlkit layout-preserving
        writer, which drops the `_group` marker (underscore keys are
        transient in-memory state) and wrote the expansion out BESIDE the
        group rows — so the next `load` expanded the group on top of its own
        output and the params doubled. The feature corrupted the manifest on
        first use.
        """
        from just_makeit._property import run as property_run

        _quiet(property_run, grouped, "frame", "gain", None, "float", False)
        text = self._manifest_text(grouped)
        assert "[[frame.init_params]]" not in text, text
        assert text.count("[[frame.init_groups]]") == 2

    def test_a_mutating_command_does_not_duplicate_the_params(self, grouped):
        """The symptom, stated as the property that matters.

        Asserted separately from the file contents because THIS is what a
        user meets: a constructor that grew a second copy of every grouped
        argument.
        """
        from just_makeit._property import run as property_run

        before = [p["name"] for p in C.load(grouped)["frame"]["init_params"]]
        _quiet(property_run, grouped, "frame", "gain", None, "float", False)
        assert [
            p["name"] for p in C.load(grouped)["frame"]["init_params"]
        ] == before

    def test_a_hand_written_param_beside_a_group_still_round_trips(
        self, tmp_path
    ):
        """Only the grouped ones are skipped, not the whole list."""
        root = _project(
            tmp_path,
            "mix",
            '\n[[frame.init_params]]\nname = "crc"\ntype = "int"\n' + GROUPED,
            group=True,
        )
        _quiet(_apply.run, root)
        text = self._manifest_text(root)
        assert 'name = "crc"' in text
        assert "preamble_kind" not in text


class TestTheKeysAreRecognised:
    def test_no_unknown_key_warning(self, grouped):
        unknown = _keys.unknown_keys(C.load(grouped))
        assert not [
            u for u in unknown if "group" in str(u) or "prefix" in str(u)
        ], unknown

    def test_a_typo_on_the_row_IS_reported(self, tmp_path):
        """The guard against over-accepting.

        A row is two keys — which group, under what prefix. Everything else
        belongs to the group's own field declarations, so a stray key there
        is a mistake worth naming.
        """
        root = _project(
            tmp_path,
            "typo",
            '\n[[frame.init_groups]]\ngroup = "wfm_seq"\nprfix = "x"\n',
            group=True,
        )
        unknown = _keys.unknown_keys(C.load(root))
        assert any("prfix" in str(u) for u in unknown), unknown

    def test_group_is_not_read_as_a_component(self, grouped):
        """`[[group]]` is a top-level SSOT table, like `[[enum]]`.

        Missing from the reserved list it is silently treated as an object,
        and `cfg["group"].get(...)` raises on a list — which is exactly what
        happened before it was added.
        """
        assert "group" not in C.components(C.load(grouped))
        assert "group" in C.RESERVED_SECTIONS


class TestEveryReaderSeesTheExpansion:
    """The registration-free gate on WHERE the expansion runs.

    Ten sites read `.get("init_params")` straight off the merged config. An
    expansion behind `C.init_params` would reach the callers of that accessor
    and silently miss the rest — the shape of a feature that works in the
    demo and not in `jm apply`.

    This does not name those sites. It asserts that the expansion has already
    happened by the time `load` returns, which is what makes every reader,
    present and future, correct without knowing about groups at all.
    """

    def test_load_returns_the_expanded_list(self, grouped):
        raw = C.load(grouped)["frame"]["init_params"]
        assert [p["name"] for p in raw] == [
            "preamble_kind",
            "preamble_len",
            "sync_kind",
            "sync_len",
        ]

    def test_the_unmerged_manifest_still_holds_the_declaration(self, grouped):
        """`load_manifest` is the "inspect the file" reader and must NOT
        expand — that is what lets the writer round-trip the group."""
        raw = C.load_manifest(grouped)
        assert "init_params" not in raw.get("frame", {})

    def test_the_gate_finds_the_raw_readers_it_exists_for(self):
        """Armed: if nothing reads `init_params` raw any more, this gate is
        describing a hazard that no longer exists and should be revisited
        rather than left passing on nothing."""
        src = Path(__file__).parent.parent / "src" / "just_makeit"
        raw = 0
        for path in list(src.glob("*.py")) + list(src.glob("_context/*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "init_params"
                ):
                    raw += 1
        assert raw >= 5, (
            f"only {raw} raw `.get('init_params')` reader(s) found — if the "
            "manifest now has one accessor, expanding there would be simpler "
            "and this gate's premise no longer holds."
        )
