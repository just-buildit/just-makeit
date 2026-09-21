"""gh-1448: the ownership token survives the project's own formatter.

This is the fourth detector today to read FORMATTED C. `_norm_unit`, the
adjacent-literal collapse and the `out=` guard pattern each broke on GNU
layout, every time on a shape the fixture never produced. The token is one
short line precisely so there is nothing for clang-format to reflow -- and
this is the fixture that proves it, with `ReflowComments` on.

Lives on the PROJECT_ENV_TESTS path because it needs the pinned
clang-format (gh-1442): on the isolated path it would not be on PATH.

GATE: an owned fragment's ownership token survives the project's formatter.
"""

from __future__ import annotations


from _jmrun import run_cli

from test_gh1448_adopt_flip import FRAG, TOKEN, _key, _project


class TestInTheProjectsOwnHouseStyle:
    def test_the_token_survives_clang_format(self, tmp_path):
        """The fourth detector today to read formatted C. `_norm_unit`, the
        literal collapse and the `out=` guard pattern each broke on GNU
        layout; one short line has nothing to reflow."""
        proj = _project(tmp_path, generated=False)
        (proj / ".clang-format").write_text(
            "BasedOnStyle: GNU\nColumnLimit: 79\nReflowComments: true\n"
        )
        cfg = proj / "just-makeit.toml"
        cfg.write_text(
            cfg.read_text().replace(
                "[project]", '[project]\nc_style = "clang-format"', 1
            )
        )
        _key(proj)
        assert run_cli("apply", cwd=proj).returncode == 0
        assert (proj / FRAG).read_text().splitlines()[0] == TOKEN
        # ...and a second apply is steady state, not a refused adoption.
        assert run_cli("apply", cwd=proj).returncode == 0
        assert run_cli("status", "--check", cwd=proj).returncode == 0
