"""End-to-end test: NCO tone generator backed by doppler's nco_state_t.

Demonstrates:
  - [project] find_packages = ["Doppler"]  — managed external-deps block
  - [tone] extra_link_libs = ["doppler::doppler-static"]  — standalone linking
  - opaque state (nco_state_t*) with create_impl / destroy_impl
  - jm apply keeping the find_package() call alive across re-runs

doppler can be supplied three ways, tried in order:
  1. --doppler-prefix PATH on the command line (or argument to run()).
  2. A local install / build tree discoverable by _find_doppler_prefix().
  3. Auto-download of the prebuilt release tarball into a cache dir
     (~/.cache/jm-tests/doppler/v<version>/). Skips if the download
     fails (no network, asset name mismatch on this platform, etc.).

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
_DOPPLER_VERSION = "0.39.0"
_DOPPLER_RELEASE_URL = (
    "https://github.com/doppler-dsp/doppler/releases/download/"
    "v{version}/doppler-{version}-{platform}.tar.gz"
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


def _platform_tag() -> str | None:
    """Return the doppler release-asset platform tag for this host.

    The release naming convention is doppler-<version>-<platform>.tar.gz.
    Returns None when the current platform doesn't match a known tag."""
    import platform as _platform

    system = sys.platform
    machine = _platform.machine().lower()
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
        if machine in ("aarch64", "arm64"):
            return "linux-aarch64"
    if system == "darwin":
        if machine in ("x86_64", "amd64"):
            return "darwin-x86_64"
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
    return None


def _cache_dir() -> Path:
    """The per-user cache directory for jm-test downloads."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "jm-tests" / "doppler"


def _download_doppler(version: str = _DOPPLER_VERSION) -> str | None:
    """Download + extract the doppler prebuilt tarball into the cache.

    Returns the prefix path (the directory containing lib/cmake/doppler/)
    on success, or None if the download couldn't be completed (no
    network, no matching asset for this platform, extraction failed)."""
    platform = _platform_tag()
    if platform is None:
        return None

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

    url = _DOPPLER_RELEASE_URL.format(version=version, platform=platform)
    extract_dir.mkdir(parents=True, exist_ok=True)
    tarball = extract_dir.parent / f"doppler-{version}-{platform}.tar.gz"
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
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(extract_dir)
    except (tarfile.TarError, OSError) as exc:
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


def _find_doppler_prefix() -> str | None:
    """Return the doppler prefix to pass to --doppler-prefix.

    Tries in order:
      1. A locally-installed doppler (system paths, ~/doppler/build).
      2. The auto-downloaded prebuilt release in the cache dir.

    Returns the prefix (i.e. the directory one level above
    lib/cmake/doppler/) or None when neither path produces a usable
    cmake config."""
    candidates = [
        Path("/usr/local"),
        Path("/usr"),
        # Rootless installs (e.g. unpacking the prebuilt
        # doppler-<ver>-<plat>.tar.gz into a user prefix) are listed before
        # the local source build tree, which may be stale or incomplete.
        Path.home() / ".local" / "doppler",
        Path.home() / ".local",
        Path.home() / "doppler" / "build",
    ]
    for prefix in candidates:
        for rel in (".", "lib/cmake/doppler", "lib64/cmake/doppler"):
            d = prefix / rel
            # gh-434: require the targets file NEXT TO the config. A doppler
            # source build tree ships doppler-config.cmake but (pre
            # doppler#380) no doppler-targets.cmake -- install(EXPORT) only
            # materialises it at install time -- so a config-only directory
            # is a false-positive prefix that hard-fails at cmake configure
            # instead of falling through to the auto-download.
            if (d / "doppler-config.cmake").exists() and (
                d / "doppler-targets.cmake"
            ).exists():
                return str(prefix)
    # Local search came up empty; fall back to the auto-download.
    return _download_doppler()


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
    "    (void)state; /* TODO: implement */\n    return (float complex)0;"
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
    # [project] must live in the manifest, not in a fragment.
    cfg = C.load(proj)
    cfg["project"]["find_packages"] = ["Doppler"]
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
    core_h = proj / "native" / "inc" / "tone" / "tone_core.h"
    h = core_h.read_text(encoding="utf-8")
    assert "nco_state_t * nco;" in h, h

    # 7. Add the doppler nco include to tone_core.h and patch step().
    # The nco include goes after clib_common.h (which is always first in
    # jm-generated headers).  The step body uses cosf/sinf so <math.h> is
    # needed too.
    h_text = core_h.read_text(encoding="utf-8")
    if '#include "nco/nco_core.h"' not in h_text:
        h_text = h_text.replace(
            '#include "clib_common.h"',
            '#include "clib_common.h"\n#include "nco/nco_core.h"\n#include <math.h>',
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
