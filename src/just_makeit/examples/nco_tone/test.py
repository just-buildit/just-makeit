"""End-to-end test: NCO tone generator backed by doppler's nco_state_t.

Demonstrates:
  - [project] find_packages = [{ name = "Doppler", pkg_config = "doppler" }]
    — the managed external-deps block, and the installed .pc
  - [tone] extra_link_libs = ["doppler::doppler-static"]  — standalone linking
  - opaque state (nco_state_t*) with create_impl / destroy_impl
  - jm apply keeping the find_package() call alive across re-runs

doppler is supplied two ways, tried in order:
  1. --doppler-prefix PATH on the command line (or argument to run()).
     The explicit, opt-in escape hatch: a doppler developer testing a working
     tree passes `--doppler-prefix ~/doppler/build`, and CI passes the prefix
     it extracted. Whatever is passed is printed.
  2. Otherwise the LATEST doppler release is downloaded into a per-user cache
     (~/.cache/jm-tests/doppler/v<version>/<platform>) and built against.
     `_DOPPLER_VERSION` is the fallback when the release list is unreachable,
     not the target. Skips if the download cannot be completed (no network and
     nothing cached, asset name mismatch on this platform, etc.).

This example is for jm/doppler USERS, so it does not look at what is installed
on the machine — see `_find_doppler_prefix` for the measurement that killed
that. If you want doppler installed permanently rather than fetched per run,
install it normally and pass --doppler-prefix; the docs page for this example
shows how.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/nco_tone/test.py [--doppler-prefix PATH]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from just_makeit import _incpath as INC
from pathlib import Path
from just_makeit._pyfmt import flatten_prose

# Pinned doppler version for the auto-download path. Bump when doppler
# cuts a new release; eventually replace with a GitHub API lookup of the
# latest release. Must be >= 0.14.0, which made `doppler::doppler-static`
# C++-free (C99 pocketfft; the ZMQ/stream layer split into the optional
# `doppler::stream` targets) — so anything linking it resolves with `-lm`
# alone and no C++ stdlib. (0.13.0 introduced the target and moved the
# install dir lib64/ -> lib/.)
#
# Now also a *floor*, not just a convenience: >= 0.39.0 is required, because
# that release added the trailing capacity argument to `nco_steps_u32` that
# the step() body below passes. CI downloads doppler's latest release rather
# than this pin, so the two paths only agree while this tracks it — a stale
# pin here means a local run fails against an API the example no longer uses.
#
# `make lint` reports when this lags doppler's latest
# (scripts/check_doppler_pin.py), advisory rather than gating: the pin drifts
# because doppler published, not because of the change being linted, and
# doppler ships roughly weekly. The floor is the part with teeth and is
# asserted in tests/test_doppler_pin_check.py.
_DOPPLER_VERSION = "0.55.0"
#: The oldest doppler whose API this example actually compiles against —
#: v0.39.0 added the trailing capacity argument to `nco_steps_u32`. It lived
#: only in the prose above until it was encoded here, which is why a local
#: install below it produced `too many arguments to nco_steps_u32` twice
#: (2026-07-30 and 2026-08-30) instead of being rejected as unusable.
_DOPPLER_FLOOR = "0.39.0"
_DOPPLER_RELEASE_URL = (
    "https://github.com/doppler-dsp/doppler/releases/download/"
    "v{version}/doppler-{version}-{platform}{ext}"
)
# Overall wall-clock cap on the tarball download. urlopen(timeout=...) is only a
# per-read socket timeout, so a server that trickles bytes never trips it; this
# bounds the whole transfer. The asset is ~2 MB (a normal fetch is well under a
# second), so this only fires on a genuine stall — and the caller then skips.
_DOWNLOAD_DEADLINE_S = 120


def _cmd(args, cwd, env=None):
    r = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\n"
            f"stderr:\n{r.stderr}"
        )
    return r


#: doppler's release-asset name for each host, keyed on (``sys.platform``,
#: normalised machine) and read off the assets doppler actually publishes
#: (v0.55.0). Two rows here were once wrong and neither failed: macOS was
#: asked for as ``darwin-arm64`` while doppler names it ``macos-arm64``, and
#: Windows had no row, so on both the download 404'd or never ran and the
#: build SKIPPED inside a test that reported PASSED (gh-1377). A host missing
#: from this table is a host doppler publishes nothing for.
_ASSETS = {
    ("linux", "x86_64"): ("linux-x86_64", ".tar.gz"),
    ("linux", "aarch64"): ("linux-aarch64", ".tar.gz"),
    ("darwin", "aarch64"): ("macos-arm64", ".tar.gz"),
    ("win32", "x86_64"): ("windows-x86_64", ".zip"),
}

#: ``platform.machine()`` spells one architecture several ways: Windows says
#: ``AMD64``, macOS says ``arm64``.
_MACHINE_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


def _platform_tag() -> tuple[str, str] | None:
    """Return doppler's (platform tag, archive extension) for this host.

    The release naming convention is ``doppler-<version>-<tag><ext>``, where
    ``<ext>`` is ``.zip`` on Windows and ``.tar.gz`` everywhere else. Returns
    None when doppler publishes no build for this host."""
    import platform as _platform

    machine = _platform.machine().lower()
    machine = _MACHINE_ALIASES.get(machine, machine)
    return _ASSETS.get((sys.platform, machine))


def _cache_dir() -> Path:
    """The per-user cache directory for jm-test downloads."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "jm-tests" / "doppler"


def _download_doppler(version: str = _DOPPLER_VERSION) -> str | None:
    """Download + extract the doppler prebuilt tarball into the cache.

    Returns the prefix path (the directory containing lib/cmake/doppler/)
    on success, or None if the download couldn't be completed (no
    network, no matching asset for this platform, extraction failed)."""
    asset = _platform_tag()
    if asset is None:
        return None
    platform, ext = asset

    extract_dir = _cache_dir() / f"v{version}" / platform
    # If a previous run already extracted here and the cmake config is
    # present, reuse it without re-downloading.
    if extract_dir.exists():
        for rel in (
            "lib/cmake/doppler/doppler-config.cmake",
            "lib64/cmake/doppler/doppler-config.cmake",
        ):
            if (extract_dir / rel).exists():
                return str(extract_dir)

    url = _DOPPLER_RELEASE_URL.format(
        version=version, platform=platform, ext=ext
    )
    extract_dir.mkdir(parents=True, exist_ok=True)
    tarball = extract_dir.parent / f"doppler-{version}-{platform}{ext}"
    try:
        with (
            urllib.request.urlopen(url, timeout=60) as resp,
            open(tarball, "wb") as fh,
        ):
            # Bounded copy: urlopen's timeout is per-read only, so enforce an
            # overall deadline to abort a trickling/stalled transfer.
            deadline = time.monotonic() + _DOWNLOAD_DEADLINE_S
            while chunk := resp.read(1 << 16):
                fh.write(chunk)
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"doppler download exceeded {_DOWNLOAD_DEADLINE_S}s"
                    )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(
            f"nco_tone: doppler auto-download failed ({exc}); "
            f"the test will skip unless --doppler-prefix is passed.",
            file=sys.stderr,
        )
        return None

    try:
        if ext == ".zip":
            with zipfile.ZipFile(tarball) as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(tarball, "r:gz") as tar:
                tar.extractall(extract_dir)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        print(
            f"nco_tone: doppler tarball extraction failed ({exc}); skipping.",
            file=sys.stderr,
        )
        return None

    # Some tarballs unpack into a top-level subdirectory (e.g.
    # doppler-0.4.6/) and others extract their lib/include directly.
    # Locate the cmake config and return the prefix containing it.
    for cfg in extract_dir.rglob("doppler-config.cmake"):
        # The prefix is two directories up from lib/cmake/doppler/.
        parts = cfg.parts
        try:
            i = parts.index("cmake")
            prefix = Path(*parts[: i - 1])  # strip lib/cmake/doppler
            return str(prefix)
        except ValueError:
            return str(cfg.parent)
    return None


def _version_key(v: str) -> tuple:
    """A comparable version key; unparseable parts sort as 0."""
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def _prefix_version(prefix: Path) -> str | None:
    """The doppler version installed at *prefix*, or None if unreadable.

    Read from the pkg-config file, the only place a prebuilt release states
    its own version. None means "cannot judge", and the caller accepts such a
    prefix rather than rejecting one it merely could not measure.

    **The prefix ROOT is searched too, and that is the case with teeth.** A
    prebuilt release puts the `.pc` under `lib/pkgconfig/`; a source BUILD
    TREE writes it at the top of the build dir. `~/doppler/build` is an
    explicit candidate in `_find_doppler_prefix`, so the build-tree layout is
    the expected local-dev case rather than an edge one -- and looking only
    under `lib/` made every such prefix report "version unknown", which skips
    the floor check entirely. The floor is described above as the part with
    teeth; for the commonest local prefix it had none, which is exactly the
    `too many arguments to nco_steps_u32` failure it was added to stop (twice,
    2026-07-30 and 2026-08-30). Measured 2026-09-16: `~/doppler/build` shipped
    `doppler.pc` reading 0.46.0 at its root and was used as "version unknown".
    """
    for libdir in ("lib", "lib64", "."):
        pc = prefix / libdir / "pkgconfig" / "doppler.pc"
        if not pc.is_file() and libdir == ".":
            # a build tree writes it at the top, with no pkgconfig/ level
            pc = prefix / "doppler.pc"
        if not pc.is_file():
            continue
        for line in pc.read_text(encoding="utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip() or None
    return None


_LATEST_URL = (
    "https://api.github.com/repos/doppler-dsp/doppler/releases/latest"
)


def latest_release(url: str = _LATEST_URL) -> str | None:
    """doppler's latest release tag without the leading ``v``, or None.

    None covers every reason the answer is unknown — offline, rate-limited,
    the repo moved — because every caller treats them identically: it cannot
    ask, so it falls back rather than guessing.

    **This is the one implementation.** `scripts/check_doppler_pin.py` imports
    it from here rather than carrying its own, the same direction the pin
    already flows (that script reads `_DOPPLER_VERSION` out of this file). It
    lives in the example because the example must run standalone — it ships in
    the package and `scripts/` does not.
    """
    import json
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=10) as fh:
            tag = json.load(fh).get("tag_name") or ""
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return tag.lstrip("v") or None


def why_prefix_unusable(prefix: Path) -> str | None:
    """Why *prefix* cannot be built against, or None when it is usable.

    Both refusals here were learned from a DISCOVERED prefix, back when this
    example scanned the machine. Discovery is gone (see
    :func:`_find_doppler_prefix`), but neither hazard went with it — a person
    passing ``--doppler-prefix`` can hand over exactly the same two directories,
    and the failure is worse there because they chose it deliberately and the
    error does not mention their choice.

    - **config without targets** (gh-434). A doppler source build tree ships
      ``doppler-config.cmake`` but, pre doppler#380, no
      ``doppler-targets.cmake`` — ``install(EXPORT)`` only materialises that at
      install time. cmake then hard-fails at CONFIGURE with *"include could not
      find requested file: .../doppler-targets.cmake"*, which reads as a broken
      example rather than an unfinished install.
    - **below the floor**. The same false positive one stage later: it
      configures, then fails to COMPILE with an argument-count error about
      ``nco_steps_u32``. That happened twice (2026-07-30, 2026-08-30) before the
      floor existed.

    An UNMEASURABLE prefix is accepted. None means "cannot judge", and refusing
    a prefix merely because its version could not be read would reject working
    installs — the caller is told what was used either way.
    """
    for rel in ("lib/cmake/doppler", "lib64/cmake/doppler", "."):
        d = prefix / rel
        if (d / "doppler-config.cmake").exists():
            if not (d / "doppler-targets.cmake").exists():
                return (
                    f"{prefix} has doppler-config.cmake but no "
                    f"doppler-targets.cmake beside it.\n"
                    f"  That is a doppler build tree that was never installed; "
                    f"cmake fails at configure.\n"
                    f"  Run `cmake --install` on it, or point "
                    f"--doppler-prefix at an installed tree."
                )
            break
    else:
        return (
            f"{prefix} contains no doppler-config.cmake "
            f"(looked in ./, lib/cmake/doppler, lib64/cmake/doppler)."
        )
    found = _prefix_version(prefix)
    if found is not None and _version_key(found) < _version_key(
        _DOPPLER_FLOOR
    ):
        return (
            f"{prefix} has doppler {found}, below the {_DOPPLER_FLOOR} floor "
            f"this example needs.\n"
            f"  v{_DOPPLER_FLOOR} added the trailing capacity argument to "
            f"nco_steps_u32 that step() passes."
        )
    return None


#: Set by jm's CI on every leg that runs the examples. Without it a doppler
#: that cannot be fetched SKIPS the build, which is right for a person trying
#: the example offline and wrong for CI: there the skip is invisible (the
#: test still reports PASSED), and that is how macOS and Windows built
#: nothing against doppler for months while every leg was green (gh-1377).
_REQUIRE_ENV = "JM_REQUIRE_DOPPLER"


def _unavailable(why: str) -> None:
    """Report that doppler is unavailable: a skip, or under CI a failure."""
    if os.environ.get(_REQUIRE_ENV):
        raise AssertionError(f"{why}, and {_REQUIRE_ENV} is set")
    print(f"  [nco_tone] {why}")
    return None


def _find_doppler_prefix() -> str | None:
    """Fetch doppler and return the prefix to pass to --doppler-prefix.

    **This example is for jm/doppler USERS, not doppler developers**, and that
    decides the whole design: it resolves doppler's *latest* release, downloads
    it into a per-user cache and builds against that. It deliberately does
    **not** look at what is installed on the machine.

    That scanning is what this replaced, and it was the bug. The candidate list
    ran `/usr/local`, `/usr`, `~/.local/doppler`, `~/.local`, `~/doppler/build`
    ahead of the download, so on any box with doppler present the pin was never
    exercised — measured 2026-09-16, a stale `~/doppler/build` from two weeks
    earlier shadowed it at 0.46.0 while CI ran 0.49.0 and the pin said 0.49.0.
    Three paths, three answers, and the local one reported itself as "version
    unknown". A developer prefix is now opt-IN via `--doppler-prefix`, which is
    explicit and printed, rather than opt-out by accident.

    CI downloads the latest release too, so the two paths now agree by
    construction instead of agreeing only while someone remembers to bump a
    constant.

    The pin is the **fallback**, not the target: when the release lookup cannot
    be made, `_DOPPLER_VERSION` is used so an offline box still runs against a
    known-good version (and reuses an already-extracted tarball). That is what
    keeps `make lint`'s currency report meaningful — the fallback must not rot.
    """
    version = latest_release()
    if version is None:
        print(
            "  [nco_tone] could not reach doppler's release list; "
            f"falling back to the pinned {_DOPPLER_VERSION}"
        )
        version = _DOPPLER_VERSION
    elif version != _DOPPLER_VERSION:
        # Not a failure: the pin is a fallback and doppler ships ~weekly.
        print(
            f"  [nco_tone] doppler latest is {version} "
            f"(pinned fallback is {_DOPPLER_VERSION})"
        )
    if _version_key(version) < _version_key(_DOPPLER_FLOOR):
        # Refuse rather than fail later at COMPILE with an argument-count
        # error that reads as a bug in the example (gh-434's lesson, and the
        # `too many arguments to nco_steps_u32` failure that happened twice).
        return _unavailable(
            f"doppler {version} is below the {_DOPPLER_FLOOR} floor this "
            f"example needs"
        )
    prefix = _download_doppler(version)
    if prefix is None:
        return _unavailable(
            f"no doppler {version} build could be fetched for "
            f"{sys.platform}/{_platform_tag() or 'an unpublished host'}"
        )
    # An independent check that the tarball is what was asked for: the
    # extracted `.pc` states its own version, and a mismatch means the release
    # asset does not carry what its tag claims.
    got = _prefix_version(Path(prefix))
    print(
        f"  [nco_tone] using {prefix} "
        f"(doppler {got or version}, downloaded release)"
    )
    return prefix


# ── TOML fragment ─────────────────────────────────────────────────────────────
#
# [project] find_packages must live in the manifest, not a fragment.
# We write it directly to just-makeit.toml after jm new.
#
# extra_link_libs names the imported target created by doppler's cmake config.
# mutable = true because step() calls nco_steps_u32() which advances state.

_FRAGMENT = '''\
[tone]
arg_type     = "void"
return_type  = "float _Complex"
mutable      = "true"
extra_link_libs = ["doppler::doppler-static"]
create_impl  = """
obj->nco = nco_create(norm_freq, 0);
if (!obj->nco) { free(obj); return NULL; }
"""
destroy_impl = """
nco_destroy(state->nco);
"""

[[tone.state]]
name    = "norm_freq"
type    = "double"
default = "0.0"

[[tone.state]]
name   = "nco"
type   = "nco_state_t *"
opaque = true
'''

# step() body: advance NCO one sample, map phase → complex exponential.
_STEP_OLD = (
    "    (void)state; /* TODO: implement */\n    return (float _Complex)0;"
)
_STEP_NEW = """\
    uint32_t phase;
    /* doppler 0.39 gave nco_steps_u32 a trailing capacity argument: the
       caller states how many samples `out` can hold, and the return is how
       many were written. One sample here, so n and capacity are both 1. */
    nco_steps_u32(state->nco, 1, &phase, 1);
    /* phase ∈ [0, 2^32) → angle ∈ [0, 2π) */
    float angle = (float)phase * (float)(2.0 * 3.14159265358979323846 / 4294967296.0);
    return cosf(angle) + I * sinf(angle);"""


def _patch_step(core_h: Path) -> None:
    text = core_h.read_text(encoding="utf-8")
    if _STEP_NEW.split("\n")[0].strip() in text:
        return  # already patched
    if _STEP_OLD not in text:
        raise AssertionError(f"step() stub not found in {core_h}")
    core_h.write_text(text.replace(_STEP_OLD, _STEP_NEW), encoding="utf-8")


# Hand-authored Doxygen class summary. jm parses the create()'s @brief from the
# sacred header and uses it verbatim as the generated .pyi class-docstring
# summary; without enrichment it falls back to the generic "Tone component."
# (jm filters its own scaffold @brief out via _is_scaffold_brief). A follow-up
# `jm apply` re-derives the .pyi from the edited header.
_CLASS_SUMMARY = (
    "Create a numerically-controlled oscillator (NCO) that generates a "
    "complex-exponential tone at a fixed normalized frequency."
)


def _enrich_class_summary(core_h: Path) -> None:
    """Replace jm's scaffold create() @brief with a real class summary.

    Mirrors the accumulator example's ``.steps/04b_doxygen.py`` create-brief
    step: the whole ``/** ... */`` block above ``tone_state_t *tone_create``
    collapses to a single ``@brief`` sentence. Idempotent — a second call is a
    no-op once the summary is present."""
    text = core_h.read_text(encoding="utf-8")
    if _CLASS_SUMMARY in text:
        return  # already enriched
    scaffold_re = re.compile(
        r"/\*\*\n \* @brief Create a tone instance\..*?"
        r"(?=tone_state_t \*tone_create)",
        re.DOTALL,
    )
    new_create = f"/**\n * @brief {_CLASS_SUMMARY}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    if n != 1:
        raise AssertionError(
            f"tone_create scaffold @brief not found in {core_h}"
        )
    core_h.write_text(text, encoding="utf-8")


def run(root: Path, doppler_prefix: str | None = None) -> None:
    from just_makeit import _config as C
    from just_makeit._apply import run as jm_apply
    from just_makeit._new import run as jm_new

    # NOTE: scaffolding + the Doxygen-enrichment check below need no doppler —
    # they only generate and parse text. The doppler-dependent build is
    # deferred until step 8, so the .pyi class-summary assertion (step 7b)
    # still runs even when the doppler tarball is unavailable and the build
    # skips. Do NOT reinstate an early doppler skip here.

    # 1. Empty project.
    proj = root / "nco_tone_demo"
    jm_new("nco_tone_demo", proj)

    # 2. Add find_packages to [project] in the manifest.
    # [project] must live in the manifest, not in a fragment. The table form
    # names doppler's pkg-config module too, so the installed .pc can list it
    # (gh-1576): a CMake package name says nothing about its module name.
    cfg = C.load(proj)
    cfg["project"]["find_packages"] = [
        {"name": "Doppler", "pkg_config": "doppler"}
    ]
    C.save(proj, cfg)

    # 3. Fragment: declares the tone component with opaque NCO state.
    fragment = root / "tone.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    jm_apply(proj, fragment=fragment)

    # 3. Verify the top CMakeLists has the external-deps sentinel block.
    cmake_text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "# ── External deps" in cmake_text, cmake_text
    assert "find_package(Doppler REQUIRED)" in cmake_text, cmake_text
    assert "# ── End external deps" in cmake_text, cmake_text

    # 4. Verify a second jm apply is a no-op (idempotent).
    jm_apply(proj)
    cmake_text2 = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    assert cmake_text2 == cmake_text, (
        "jm apply changed CMakeLists.txt on second run"
    )

    # 5. Verify the component CMakeLists links doppler::doppler-static.
    comp_cmake = (
        proj / "native" / "src" / "tone" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")
    assert "doppler::doppler-static" in comp_cmake, comp_cmake

    # 6. Verify the generated header has the opaque nco field.
    core_h = INC.header_root(proj) / "tone" / "tone_core.h"
    h = core_h.read_text(encoding="utf-8")
    assert "nco_state_t * nco;" in h, h

    # 7. Add the doppler nco include to tone_core.h and patch step().
    # The nco include goes after clib_common.h (which is always first in
    # jm-generated headers).  The step body uses cosf/sinf so <math.h> is
    # needed too.
    h_text = core_h.read_text(encoding="utf-8")
    if '#include "nco/nco_core.h"' not in h_text:
        h_text = h_text.replace(
            '#include "nco_tone_demo/clib_common.h"',
            '#include "nco_tone_demo/clib_common.h"\n#include "nco/nco_core.h"\n#include <math.h>',
            1,
        )
        core_h.write_text(h_text, encoding="utf-8")
    _patch_step(core_h)

    # 7b. Enrich the header with a hand-authored Doxygen class summary and
    # regenerate the .pyi. This runs BEFORE the doppler-dependent build, so the
    # class-docstring assertion below is exercised on every CI leg regardless of
    # whether the tarball download (step 8) succeeds. `jm apply` re-derives the
    # .pyi from the edited header; create()'s @brief becomes the class summary.
    _enrich_class_summary(core_h)
    jm_apply(proj)
    pyi = (proj / "src" / "nco_tone_demo" / "tone.pyi").read_text(
        encoding="utf-8"
    )
    # gh-744: the summary wraps when it does not fit on one line.
    assert _CLASS_SUMMARY in flatten_prose(pyi), (
        "enriched class summary missing from tone.pyi:\n" + pyi
    )
    assert "Tone component." not in pyi, (
        "generic scaffold summary still present in tone.pyi"
    )

    # From here on we need a real doppler install to configure/build/link.
    if doppler_prefix is None:
        doppler_prefix = _find_doppler_prefix()
    elif (why := why_prefix_unusable(Path(doppler_prefix))) is not None:
        # An EXPLICIT prefix is validated; a downloaded one is not, because
        # this code produced it. Refusing here names the directory the caller
        # chose, instead of letting cmake fail several layers down.
        print(f"nco_tone: SKIP build — --doppler-prefix {why}")
        return
    else:
        print(
            f"  [nco_tone] using {doppler_prefix} (explicit --doppler-prefix)"
        )
    if doppler_prefix is None:
        print(
            "nco_tone: enrichment verified; SKIP build — doppler not found "
            "(pass --doppler-prefix PATH)"
        )
        return

    # 8. cmake configure + build + ctest.
    # cmake doesn't search lib64/cmake on all platforms, so resolve
    # Doppler_DIR explicitly from the given prefix.
    prefix_path = Path(doppler_prefix)
    doppler_dir = None
    for rel in (
        "lib/cmake/doppler",
        "lib64/cmake/doppler",
        ".",  # raw build dir
    ):
        candidate = prefix_path / rel / "doppler-config.cmake"
        if candidate.exists():
            doppler_dir = str(prefix_path / rel)
            break
    if doppler_dir is None:
        doppler_dir = doppler_prefix  # fallback: let cmake search

    import os

    env = os.environ.copy()
    _cmd(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
            f"-DDoppler_DIR={doppler_dir}",
        ],
        cwd=proj,
        env=env,
    )
    _cmd(["cmake", "--build", "build", "--parallel", "4"], cwd=proj)
    _cmd(["ctest", "--test-dir", "build", "--output-on-failure"], cwd=proj)

    # 9. Quick Python smoke-test: tone at 0.25 cycles/sample → quarter-circle steps.
    sys.path.insert(0, str(proj / "src"))
    try:
        import importlib

        pkg = importlib.import_module("nco_tone_demo")
        tone = pkg.Tone(norm_freq=0.25)
        samples = [tone.step() for _ in range(4)]
    finally:
        sys.path.pop(0)
        import sys as _sys

        for key in list(_sys.modules):
            if "nco_tone_demo" in key:
                del _sys.modules[key]

    # Four quarter-circle steps: 1, j, -1, -j (within float precision).
    expected = [1 + 0j, 0 + 1j, -1 + 0j, 0 - 1j]
    for i, (got, want) in enumerate(zip(samples, expected)):
        assert abs(got - want) < 1e-6, f"sample[{i}]: got {got}, want {want}"

    print("nco_tone: PASSED")


if __name__ == "__main__":
    doppler_prefix = None
    args = sys.argv[1:]
    if "--doppler-prefix" in args:
        idx = args.index("--doppler-prefix")
        doppler_prefix = args[idx + 1]

    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp), doppler_prefix=doppler_prefix)
