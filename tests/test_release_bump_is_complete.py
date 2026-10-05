"""`make bump-version` writes every copy of the version, and nothing else.

It used to write `pyproject.toml` alone, so the `sync-version` and `uv-lock`
pre-commit hooks then rewrote `bootstrap.toml` and `uv.lock` and **aborted the
first `git commit` of every release**. Re-running the identical commit worked,
so it was survivable — and it had been survived often enough to be written into
the release runbook as expected behaviour, which is what a papercut looks like
once it has stopped being read as a defect.

It was never harmless. A pre-commit hook that aborts a commit does not stop the
next command in the script, so `git commit -am ... ; git push` pushes the
UNCHANGED head — a foot-gun the same runbook warns about separately, and which
this behaviour armed on every release. The rule "the release commit is four
files; two means you pushed a half-bump" existed only because the bump did not
write four files.

The gate below runs the real script against a throwaway copy of the
manifests rather than reading the Makefile and agreeing with itself — a check
that can only fail by coincidence is a description, not a control.

Running the script under `sys.executable` is what caught that it hard-imported
`tomllib` — fine in the dev env (3.12) where it had only ever run as a
pre-commit hook, and a `ModuleNotFoundError` on the 3.9 and 3.10 legs of the
CI matrix. A script exercised on one interpreter is untested on the other five.

uv.lock, and nothing else (gh-1866)
-----------------------------------
`uv.lock` carries the version too, on the project's own `[[package]]` entry.
The bump left it to uv: it ran `sync_version.py` under a plain `uv run`, then
`uv lock`, and each re-locks a lock that is behind pyproject.toml's version.
A re-lock rewrites the whole file and stamps the lockfile `revision` of the uv
that ran it -- 3 up to uv 0.12.21, 5 from 0.12.22, whatever the lock said
before (measured 2026-10-05 on 0.11.28, 0.12.21, 0.12.22 and 0.12.23). Cutting
v0.98.1 with uv 0.12.23 changed `revision = 3` too, `make ci-changes` read the
release commit as more than a bump, and its CI ran the full matrix.

`TestTheBumpIsABumpAlone` runs the REAL `make bump-version` -- and the real
pre-commit entries, for a version edited by hand -- in a copy of this working
tree, with whatever uv is on PATH, against a lock whose revision no released
uv writes (`FOREIGN_REVISION`). Any re-lock, by any uv, moves that line, so
the gate does not depend on the local uv happening to be newer than the lock.
Whether the release commit is a bump alone is then asked of the standard's
own `make ci-changes`, the check the fast path reads, not restated here.

It runs offline (`UV_OFFLINE`): `uv lock --check` needs no network, and
sync_version runs under `uv run --no-sync`, which needs no env, in a private
one that inherits this interpreter's packages (tomli below 3.11).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import _inheritable_paths

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - version-dependent
    import tomli as tomllib

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "sync_version.py"
# Every file sync_version reads or writes.
MANIFESTS = ("pyproject.toml", "bootstrap.toml", "uv.lock")
# A lockfile `revision` no released uv writes: every uv measured stamps its
# own (3 or 5) on a re-lock, whatever the lock said, and accepts any value
# from a lock it leaves alone. So a re-lock under ANY uv moves this line.
FOREIGN_REVISION = 99


def _run(root: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *flags],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _fixture(tmp_path: Path, version: str = "9.9.9") -> Path:
    """A copy of the manifests with pyproject already bumped.

    Copies the real files so the regexes are exercised against the shapes that
    actually ship, not a hand-written approximation of them.
    """
    for name in MANIFESTS:
        shutil.copy2(REPO / name, tmp_path / name)
    pp = tmp_path / "pyproject.toml"
    text = pp.read_text(encoding="utf-8")
    bumped, n = _sub_version(text, version)
    assert n == 1, "pyproject.toml's version line did not match"
    pp.write_text(bumped, encoding="utf-8")
    return tmp_path


def _sub_version(text: str, version: str) -> tuple[str, int]:
    return re.subn(
        r'^version = "[^"]*"',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )


def _version_of(path: Path) -> str:
    m = re.search(
        r'^version\s*=\s*"([^"]*)"',
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert m, f"no version line in {path.name}"
    return m.group(1)


def _lock(root: Path) -> dict:
    return tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))


def _lock_version(root: Path) -> str:
    """The project's own version in uv.lock, read as TOML.

    Found by its source -- the editable root, `.` -- rather than by name, so
    this does not share sync_version's way of finding the line it checks.
    """
    own = [
        p
        for p in _lock(root)["package"]
        if p.get("source") == {"editable": "."}
    ]
    assert len(own) == 1, f"{len(own)} editable root entries in uv.lock"
    return own[0]["version"]


def _changed_lines(before: str, after: str) -> list[str]:
    """The lines of *after* that differ from *before*, which has as many."""
    old, new = before.splitlines(), after.splitlines()
    assert len(old) == len(new), "the lock gained or lost lines"
    return [b for a, b in zip(old, new) if a != b]


def test_the_bump_carries_every_copy(tmp_path):
    """The write itself — with `--exit-zero`, as `bump-version` calls it."""
    root = _fixture(tmp_path)
    r = _run(root, "--exit-zero")
    assert r.returncode == 0, (
        f"bump path must not fail on a change:\n{r.stdout}"
    )
    assert _version_of(root / "bootstrap.toml") == "9.9.9"
    assert _lock_version(root) == "9.9.9"


def test_the_lock_changes_by_its_version_line_alone(tmp_path):
    """gh-1866: the one line, so the revision is whatever it was."""
    root = _fixture(tmp_path)
    assert _run(root, "--exit-zero").returncode == 0
    before = (REPO / "uv.lock").read_text(encoding="utf-8")
    after = (root / "uv.lock").read_text(encoding="utf-8")
    assert _changed_lines(before, after) == ['version = "9.9.9"']


def test_after_the_bump_the_hook_finds_nothing(tmp_path):
    """The papercut, stated directly.

    This is the assertion that fails if `bump-version` ever stops carrying a
    copy: the hook would then have work to do at commit time, which is exactly
    the aborted first commit.
    """
    root = _fixture(tmp_path)
    assert _run(root, "--exit-zero").returncode == 0
    second = _run(root)  # no flag — the hook's own invocation
    assert second.returncode == 0, (
        "the sync-version hook still has work to do straight after a bump, so "
        f"the first release commit will abort:\n{second.stdout}"
    )
    assert second.stdout.strip() == ""


def test_the_hook_still_fails_on_an_unsynced_tree(tmp_path):
    """`--exit-zero` must not have disarmed the gate.

    Without the flag a change is still a finding — that is the pre-commit
    convention the hook relies on to make the user re-stage the file.
    """
    root = _fixture(tmp_path)
    r = _run(root)
    assert r.returncode == 1, "the hook stopped reporting an unsynced tree"
    assert "updated bootstrap.toml" in r.stdout
    assert "updated uv.lock" in r.stdout


@pytest.mark.parametrize("name", ["bootstrap.toml", "uv.lock"])
def test_a_copy_it_cannot_find_is_an_error(tmp_path, name):
    """A pattern that matches nothing is a sync that stopped syncing.

    So it fails, naming the file, rather than reporting a clean tree.
    """
    root = _fixture(tmp_path)
    path = root / name
    # The line under test: bootstrap.toml's one `version =`, or the one
    # above the editable root's `source` in uv.lock.
    own = (
        r'^version = "[^"]*"\n(?=source = \{ editable = "\." \}$)'
        if name == "uv.lock"
        else r"^version\s*=.*\n"
    )
    text, n = re.subn(
        own, "", path.read_text(encoding="utf-8"), flags=re.MULTILINE
    )
    assert n == 1, f"{name}: the version line under test is not there"
    path.write_text(text, encoding="utf-8")
    r = _run(root, "--exit-zero")
    assert r.returncode == 2, r.stdout
    assert name in r.stdout


def test_the_shipped_tree_is_already_in_sync():
    """The copies on disk agree right now.

    Cheap, and it catches a half-bump that reached `main` — the state the old
    behaviour could produce by pushing an unchanged head after an aborted
    commit.
    """
    version = _version_of(REPO / "pyproject.toml")
    assert _version_of(REPO / "bootstrap.toml") == version
    assert _lock_version(REPO) == version


# ── end to end: the real bump, the real hooks, any uv (gh-1866) ─────────────

# What the parent process must not hand the children. MAKE*: a `make test`
# parent's flags and jobserver. GITHUB_OUTPUT: `ci-changes` appends to it, and
# this test's answer is not the CI step's. VIRTUAL_ENV / UV_*: the child's env
# is set below, and a frozen or no-sync default from outside would hide the
# very re-lock this gate looks for. GIT_*: a suite run from inside a git hook
# would otherwise aim `git init` at the outer repository.
_SCRUB = re.compile(
    r"^(MAKEFLAGS|MFLAGS|MAKELEVEL|MAKEOVERRIDES|GITHUB_OUTPUT|VIRTUAL_ENV"
    r"|UV_(FROZEN|LOCKED|NO_SYNC|PROJECT_ENVIRONMENT|PYTHON|OFFLINE)|GIT_.*)$"
)


def _tree(tmp_path: Path) -> Path:
    """Every tracked file of THIS working tree, as it is on disk.

    The working tree rather than a commit, so an uncommitted edit -- a
    sabotage, or the change under review -- is what runs. The lock's
    revision is then set to `FOREIGN_REVISION`.
    """
    files = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    tree = tmp_path / "tree"
    for rel in filter(None, files.split("\0")):
        src = REPO / rel
        if not os.path.lexists(src):
            continue  # deleted in the working tree, not yet staged
        dst = tree / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    lock = tree / "uv.lock"
    text, n = re.subn(
        r"^revision = \d+$",
        f"revision = {FOREIGN_REVISION}",
        lock.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    assert n == 1, "uv.lock has no `revision` line to set"
    with open(lock, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return tree


def _env(tmp_path: Path) -> dict:
    """Offline, and a private env that inherits this interpreter's packages.

    `uv run` uses the env named by UV_PROJECT_ENVIRONMENT. Inheriting through
    a `.pth`, as conftest's per-worker env does, gives sync_version its tomli
    below 3.11 without installing anything -- and without this process's own
    env being the one a re-lock would then try to sync.
    """
    venv = tmp_path / "env"
    subprocess.run(
        ["uv", "venv", "-q", "--python", sys.executable, str(venv)],
        capture_output=True,
        check=True,
    )
    site = sorted(venv.glob("lib/python*/site-packages")) or sorted(
        venv.glob("Lib/site-packages")
    )
    (site[0] / "_jm_inherited.pth").write_text(
        "\n".join(_inheritable_paths()) + "\n", encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if not _SCRUB.match(k)}
    env.update(
        UV_OFFLINE="1",
        UV_PROJECT_ENVIRONMENT=str(venv),
        UV_PYTHON=sys.executable,
    )
    return env


def _sh(tree: Path, env: dict, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _commit(tree: Path, env: dict, message: str) -> None:
    for argv in (
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=jm-test",
            "-c",
            "user.email=jm-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            message,
        ],
    ):
        r = _sh(tree, env, *argv)
        assert r.returncode == 0, r.stderr


def _revision(tree: Path) -> int:
    return _lock(tree)["revision"]


def _out(r: subprocess.CompletedProcess) -> str:
    return f"exit {r.returncode}\n{r.stdout}\n{r.stderr}"


def _hook_entries_for(tree: Path, path: str) -> list[str]:
    """The pre-commit `entry` of every hook that *path* triggers, in order.

    Selected by `files` / `exclude`, which are regexes pre-commit searches.
    A hook with no `files` is filtered by type instead, and none of those may
    be TOML -- asserted rather than assumed, so a TOML-typed hook added later
    fails here instead of being silently left out.
    """
    import yaml

    cfg = yaml.safe_load(
        (tree / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    entries = []
    for repo in cfg["repos"]:
        for hook in repo["hooks"]:
            if "files" not in hook:
                types = hook.get("types", []) + hook.get("types_or", [])
                assert types and "toml" not in types, hook["id"]
                continue
            if not re.search(hook["files"], path):
                continue
            if "exclude" in hook and re.search(hook["exclude"], path):
                continue
            entries.append(hook["entry"])
    return entries


class TestTheBumpIsABumpAlone:
    """gh-1866: the release commit's lock diff is its version line."""

    def test_the_release_commit_reads_as_a_bump_alone(self, tmp_path):
        tree, env = _tree(tmp_path), _env(tmp_path)
        assert _sh(tree, env, "git", "init", "-q").returncode == 0
        _commit(tree, env, "base")
        r = _sh(tree, env, "make", "-s", "bump-version", "VERSION=9.9.9")
        # The revision first: a re-lock that then fails for another reason
        # (offline, say) must still be reported as the re-lock it was.
        assert _revision(tree) == FOREIGN_REVISION, (
            "the bump re-locked uv.lock, stamping the running uv's lockfile "
            f"revision over {FOREIGN_REVISION}:\n{_out(r)}"
        )
        assert r.returncode == 0, _out(r)
        assert _lock_version(tree) == "9.9.9"
        _commit(tree, env, "chore: release v9.9.9")
        r = _sh(tree, env, "make", "-s", "ci-changes", "BASE=HEAD^")
        assert r.stdout.strip() == "src=false", (
            "the release commit is not a version bump alone, so its CI runs "
            f"the full matrix:\n{_out(r)}"
        )

    def test_a_lock_that_needs_more_is_refused_not_relocked(self, tmp_path):
        """A dependency moving is refused at bump time, not found by CI.

        Before, the bump re-locked, the release commit carried the change,
        and nothing said so until `ci-changes` ran in CI.
        """
        tree, env = _tree(tmp_path), _env(tmp_path)
        pp = tree / "pyproject.toml"
        text = pp.read_text(encoding="utf-8")
        # Drop a runtime dependency: a change uv can re-lock offline, from
        # the lock alone, so a bump that still re-locks really succeeds.
        dep = tomllib.loads(text)["project"]["dependencies"][-1]
        text, n = re.subn(
            r'^\s*"' + re.escape(dep) + r'",?\n', "", text, flags=re.M
        )
        assert n == 1, f"{dep!r} is not one line of pyproject.toml"
        pp.write_text(text, encoding="utf-8")
        r = _sh(tree, env, "make", "-s", "bump-version", "VERSION=9.9.9")
        assert _revision(tree) == FOREIGN_REVISION, (
            f"the bump re-locked instead of refusing:\n{_out(r)}"
        )
        assert r.returncode != 0, (
            f"a lock needing more than its version was bumped:\n{_out(r)}"
        )
        assert "uv.lock needs more than its" in r.stdout + r.stderr, _out(r)

    def test_a_hand_bump_through_the_hooks_keeps_the_revision(self, tmp_path):
        """The other way a version changes: edited, then committed.

        The hooks pyproject.toml triggers, in their configured order, as
        pre-commit runs them -- once to fix the tree, and once more on the
        re-staged tree, which must then pass untouched.
        """
        tree, env = _tree(tmp_path), _env(tmp_path)
        pp = tree / "pyproject.toml"
        text, n = _sub_version(pp.read_text(encoding="utf-8"), "9.9.9")
        assert n == 1
        pp.write_text(text, encoding="utf-8")
        entries = _hook_entries_for(tree, "pyproject.toml")
        assert entries, "no pre-commit hook runs on pyproject.toml"
        for entry in entries:
            _sh(tree, env, *shlex.split(entry))  # a fix exits 1, by design
        assert _revision(tree) == FOREIGN_REVISION, (
            "a hook re-locked uv.lock for a version change, stamping the "
            f"running uv's lockfile revision over {FOREIGN_REVISION}"
        )
        assert _lock_version(tree) == "9.9.9"
        lock = (tree / "uv.lock").read_text(encoding="utf-8")
        for entry in entries:
            r = _sh(tree, env, *shlex.split(entry))
            assert r.returncode == 0, f"{entry}: {_out(r)}"
        assert (tree / "uv.lock").read_text(encoding="utf-8") == lock
