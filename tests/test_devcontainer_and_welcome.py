"""The Codespaces sandbox greets you, and every path it prints is real.

`docker/` had no tests. Four defects lived there at once, and all four were
the kind a reader hits in their first minute:

1. `.devcontainer/devcontainer.json` set `remoteUser: root` against an image
   whose `USER` is `user` and whose home *is* the workspace folder. As root
   `$HOME` is `/root`, so every `~/examples/...` path the welcome printed did
   not exist. Verified by running the published image both ways.
2. The workspace folder held only dotfiles and `examples/`, so the editor had
   nothing to open — you landed in a blank window with no instructions.
3. The welcome's project list was hand-written. It advertised `my_corr/`,
   which no example produces, and omitted ~25 directories that do exist.
4. It told the reader to run `python3 -m just_makeit._example_readme`, a
   module that does not exist.

(3) is the disease `help-check` already guards against elsewhere in this repo:
a hand-maintained list of things the tree also knows. The fix is the same —
derive it — and these tests hold the derivation, not the list.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DOCKER = _ROOT / "docker"
_DEVCONTAINER = _ROOT / ".devcontainer" / "devcontainer.json"
_DOCKERFILE = _DOCKER / "Dockerfile.examples-linux"

sys.path.insert(0, str(_DOCKER))

from welcome import describe, main, render  # noqa: E402


def _strip_jsonc(text: str) -> str:
    """devcontainer.json permits `//` comments; json.loads does not."""
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


@pytest.fixture(scope="module")
def devcontainer() -> dict:
    return json.loads(_strip_jsonc(_DEVCONTAINER.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


class TestTheContainerAgreesWithTheImage:
    """Defect 1 — the mismatch that made every printed path wrong."""

    def test_remote_user_is_the_image_user(self, devcontainer, dockerfile):
        users = re.findall(r"^USER\s+(\S+)", dockerfile, re.M)
        assert users, "Dockerfile no longer declares a USER"
        assert devcontainer["remoteUser"] == users[-1], (
            f"devcontainer runs as {devcontainer['remoteUser']!r} but the "
            f"image's final USER is {users[-1]!r}. Their homes differ, so "
            "every `~/...` path the welcome prints breaks in Codespaces "
            "while still working under `docker run`."
        )

    def test_workspace_folder_is_the_image_home(
        self, devcontainer, dockerfile
    ):
        m = re.search(r"^ENV\s+JM_HOME=(\S+)", dockerfile, re.M)
        assert m, "Dockerfile no longer sets JM_HOME"
        assert devcontainer["workspaceFolder"] == m.group(1), (
            "the editor must open the home directory the welcome describes"
        )


class TestSomethingGreetsYou:
    """Defect 2 — a blank editor with no instructions."""

    def test_the_readme_opens_on_attach(self, devcontainer):
        settings = devcontainer["customizations"]["vscode"]["settings"]
        assert settings.get("workbench.startupEditor") == "readme", (
            "Codespaces opens an editor, not a login shell; without this the "
            "profile.d greeting can go entirely unseen"
        )

    def test_the_terminal_greets_you_too(self, devcontainer, dockerfile):
        m = re.search(r"^ENV\s+JM_HOME=(\S+)", dockerfile, re.M)
        assert (
            f"{m.group(1)}/README.md" in devcontainer["postAttachCommand"]
        ), (
            "the startup editor is a VS Code setting a user can have "
            "disabled; the terminal path must not depend on it"
        )

    def test_the_image_actually_writes_that_readme(self, dockerfile):
        """The two settings above are inert if nothing creates the file."""
        assert (
            "COPY docker/build_examples.py docker/welcome.py" in dockerfile
        ), (
            "build_examples.py imports welcome.py, so the Dockerfile must "
            "COPY it too — otherwise the build fails at image time"
        )
        assert re.search(
            r"RUN python3 /tmp/welcome\.py \$\{JM_HOME\}", dockerfile
        ), "nothing renders the README, so the editor opens an empty folder"
        assert re.search(r"test -s \$\{JM_HOME\}/README\.md", dockerfile), (
            "the render step must fail the build rather than ship a sandbox "
            "with no instructions in it"
        )

    def test_the_tutorials_symlink_is_verified_at_build_time(self, dockerfile):
        """The welcome tells every reader to `less ~/tutorials/<x>/README.md`.

        The link target is discovered from the installed package, so a base
        image on a different Python would leave it dangling — and a dangling
        symlink is not a build failure unless something checks.
        """
        assert "tutorials" in dockerfile
        assert re.search(
            r"test -r \$\{JM_HOME\}/tutorials/\w+/README\.md", dockerfile
        ), "nothing proves the tutorials symlink resolves"


class TestTheWelcomeIsDerivedNotWritten:
    """Defects 3 and 4 — a hand-written list, and a command that never ran."""

    def test_motd_holds_no_project_list(self):
        motd = (_DOCKER / "motd.sh").read_text(encoding="utf-8")
        body = "\n".join(
            ln for ln in motd.splitlines() if not ln.lstrip().startswith("#")
        )
        listed = re.findall(r"\bmy_[a-z_]+/", body)
        assert not listed, (
            f"motd.sh names project directories {sorted(set(listed))}. It is "
            "printed from the generated README precisely so that no list "
            "lives here — the last one advertised a my_corr/ that no example "
            "produces."
        )

    def test_motd_prints_the_generated_readme(self):
        motd = (_DOCKER / "motd.sh").read_text(encoding="utf-8")
        assert "README.md" in motd and "cat " in motd

    def test_no_reference_to_the_module_that_never_existed(self):
        """`just_makeit._example_readme` was cited for months and is not real."""
        import just_makeit

        pkg = Path(just_makeit.__file__).parent
        for path in (_DOCKER / "motd.sh", _DOCKER / "welcome.py"):
            # Comments only: both files name the dead module deliberately, to
            # record why the greeting is generated. Naming a mistake in a
            # comment is how it stays fixed; the gate is about what the
            # sandbox *prints*.
            text = "\n".join(
                ln
                for ln in path.read_text(encoding="utf-8").splitlines()
                if not ln.lstrip().startswith(("#", '"""', "- "))
            )
            for mod in re.findall(r"just_makeit\._([a-z_]+)", text):
                if mod in ("example_readme",):
                    pytest.fail(
                        f"{path.name} cites just_makeit._{mod}, which does "
                        "not exist — the old welcome told every sandbox user "
                        "to run it"
                    )
                assert (pkg / f"_{mod}.py").exists(), (
                    f"{path.name} cites just_makeit._{mod}, absent from the "
                    "installed package"
                )

    def test_the_page_follows_its_input(self):
        """Fed a mapping no image contains, it must describe that mapping.

        The point of the parameters: a renderer that ignored them and globbed
        the repo would pass every test written against the real examples, and
        fail here.
        """
        page = render(
            {"zeta": ["proj_z"], "alpha": ["proj_a1", "proj_a2"]},
            {"zeta": "Zeta does Z.", "alpha": "Alpha does A."},
        )
        for expected in (
            "`~/examples/proj_z/`",
            "`~/examples/proj_a1/`",
            "`~/examples/proj_a2/`",
            "Zeta does Z.",
            "Alpha does A.",
        ):
            assert expected in page, expected
        assert "my_fir" not in page, "the renderer is reading something else"

    def test_an_example_that_built_nothing_is_omitted(self):
        page = render({"ghost": [], "real": ["proj"]}, {})
        assert "ghost" not in page
        assert "proj" in page

    def test_an_unpublished_example_still_gets_a_summary(self, tmp_path):
        """gh-927: a blank cell, fixed WITHOUT publishing a regression driver.

        In this repo a `README.md` means *published to the docs gallery* — the
        `copy_examples` reconcile gate rejects one with no gallery entry. So
        the obvious fix (write READMEs for the three examples that lack them)
        would have pushed three regression drivers into a gallery curated down
        to its current set on purpose. That gate caught the attempt.

        The summary comes from the `test.py` module docstring instead: already
        written, already maintained, and inside the package — unlike
        `UNPUBLISHED`, which lives in `scripts/` and is absent from the image.
        """
        ex = tmp_path / "widget_demo"
        ex.mkdir()
        (ex / "test.py").write_text(
            '"""End-to-end test: `jm widget` does a thing worth naming.\n\n'
            'Called by tests/test_examples.py via run(root).\n"""\n',
            encoding="utf-8",
        )
        assert not (ex / "README.md").exists()
        assert describe(ex) == "`jm widget` does a thing worth naming."

    def test_the_readme_wins_when_there_is_one(self, tmp_path):
        """The docstring is a fallback, never an override."""
        ex = tmp_path / "widget_demo"
        ex.mkdir()
        (ex / "test.py").write_text('"""End-to-end test: the docstring."""\n')
        (ex / "README.md").write_text(
            "# widget_demo example\n\nThe README sentence.\n", encoding="utf-8"
        )
        assert describe(ex) == "The README sentence."

    def test_an_example_with_neither_yields_nothing(self, tmp_path):
        """Still no inventing text the tree does not own."""
        ex = tmp_path / "bare"
        ex.mkdir()
        assert describe(ex) == ""

    def test_every_example_yields_a_description(self):
        """Defect 3's other half: a blank cell for a real project.

        This was scoped to examples that *had* a `README.md`, because three
        (`app_shapes`, `bench_upgrade`, `jm_remove`) shipped without one and
        appeared in the sandbox with an empty *What it shows* cell — filed as
        gh-927 rather than explained away. They have READMEs now, so the gate
        ratchets to the property actually wanted: **every** bundled example
        yields a summary.

        Deliberately derived from the examples on disk, with no list of names
        here. A new example arrives covered, and one whose prose cannot be
        parsed fails when it is added rather than when someone opens the
        sandbox and finds a blank row.
        """
        from just_makeit._example import _EXAMPLES, _find

        missing = []
        for name in _EXAMPLES:
            ex_dir = _find(name)
            if ex_dir is None:
                continue
            if not describe(ex_dir):
                missing.append(name)
        assert not missing, (
            f"examples with no summary: {sorted(missing)}. The sandbox lists "
            "one line per project; these would be blank. Give the example a "
            "README.md whose first paragraph opens with a plain sentence."
        )

    def test_the_worked_example_is_one_a_reader_can_follow(self):
        """The footer's commands name it, so it must build AND have a tutorial.

        `less ~/tutorials/<name>/README.md` is printed to every sandbox user.
        An example without a README makes that a "No such file", and one that
        built nothing makes `just-makeit example <name>` a tour of nothing.
        """
        page = render(
            {"aaa_no_readme": ["p1"], "zzz_documented": ["p2"]},
            {"zzz_documented": "Documented."},
        )
        assert "~/tutorials/zzz_documented/README.md" in page
        assert "aaa_no_readme/README.md" not in page

    def test_the_manifest_round_trips_to_a_page(self, tmp_path):
        """`build_examples.py` writes the manifest; `welcome.py` reads it.

        Two files agreeing on one filename is the kind of seam that fails only
        inside a twenty-minute image build, where the symptom is an empty
        editor and no error anyone sees.
        """
        builder = (_DOCKER / "build_examples.py").read_text(encoding="utf-8")
        assert '".jm-built.json"' in builder, "the writer's filename moved"

        (tmp_path / ".jm-built.json").write_text(
            json.dumps(
                {
                    "built": {"fir_filter": ["my_fir"]},
                    "descriptions": {"fir_filter": "A 16-tap FIR."},
                }
            ),
            encoding="utf-8",
        )
        assert main(["welcome.py", str(tmp_path)]) == 0
        page = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "`~/examples/my_fir/`" in page
        assert "A 16-tap FIR." in page

    def test_a_missing_manifest_fails_loudly(self, tmp_path):
        """The Dockerfile checks the exit status, so it must be non-zero.

        Silently writing a page with no projects is how the sandbox would ship
        an empty tour and still build green.
        """
        assert main(["welcome.py", str(tmp_path)]) == 1
        assert not (tmp_path / "README.md").exists()

    def test_fir_filter_wins_when_it_qualifies(self):
        """The docs lead with it everywhere else; the sandbox should agree."""
        page = render(
            {"aaa_first": ["p1"], "fir_filter": ["my_fir"]},
            {"aaa_first": "Alphabetically first.", "fir_filter": "A FIR."},
        )
        assert "just-makeit example fir_filter" in page
