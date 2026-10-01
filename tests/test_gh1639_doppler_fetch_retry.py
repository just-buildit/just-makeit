"""gh-1639: nco_tone's doppler fetch survives a network blip.

v0.90.1's release lost a smoke leg to one DNS failure on one runner:
`nco_tone` fetched doppler with a single ``urlopen``, and ``JM_REQUIRE_DOPPLER``
(gh-1377) rightly turned the unfetchable doppler into a failure. Both fetches
-- the release-list lookup and the tarball download -- now go through
``_retrying``, which retries a TRANSIENT error and nothing else.

GATE: a fetch that fails transiently and then succeeds returns the success;
    a 404 fails on the first attempt, with no retry and no wait.
"""

import importlib.util
import io
import json
import socket
import tarfile
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NCO_TONE = ROOT / "src" / "just_makeit" / "examples" / "nco_tone" / "test.py"


def _load():
    spec = importlib.util.spec_from_file_location("_nco_tone_1639", NCO_TONE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dns_blip():
    # What v0.90.1's macOS runner raised, as urllib wraps it.
    return urllib.error.URLError(
        socket.gaierror(8, "nodename nor servname provided, or not known")
    )


def _http(code):
    return urllib.error.HTTPError("u", code, "status", {}, None)


class _Fake:
    """A stand-in ``urlopen``: raise each of *errors* in turn, then serve."""

    def __init__(self, errors, body):
        self.errors = list(errors)
        self.body = body
        self.calls = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return io.BytesIO(self.body)


@pytest.fixture
def mod(monkeypatch):
    m = _load()
    waits = []
    monkeypatch.setattr(m.time, "sleep", waits.append)
    m.waits = waits
    return m


def _tarball() -> bytes:
    """A minimal doppler release: just the cmake config that marks a prefix."""
    buf = io.BytesIO()
    data = b"# doppler-config\n"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(
            "doppler-9.9.9/lib/cmake/doppler/doppler-config.cmake"
        )
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_latest_release_retries_a_dns_blip(mod, monkeypatch):
    fake = _Fake([_dns_blip()], json.dumps({"tag_name": "v9.9.9"}).encode())
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    assert mod.latest_release() == "9.9.9"
    assert fake.calls == 2
    assert mod.waits == [mod._RETRY_DELAYS_S[0]]


def test_download_retries_until_it_succeeds(mod, monkeypatch, tmp_path):
    """A DNS blip, then a 503, then the real asset: three attempts."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        mod, "_platform_tag", lambda: ("linux-x86_64", ".tar.gz")
    )
    errors = [_dns_blip(), _http(503)]
    fake = _Fake(errors, _tarball())
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    prefix = mod._download_doppler("9.9.9")
    assert prefix is not None, "a transient failure was not retried"
    assert (Path(prefix) / "lib/cmake/doppler/doppler-config.cmake").exists()
    assert fake.calls == len(errors) + 1


def test_a_404_fails_at_once(mod, monkeypatch, tmp_path):
    """A missing asset is an answer, not a blip: one attempt, no wait."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        mod, "_platform_tag", lambda: ("linux-x86_64", ".tar.gz")
    )
    fake = _Fake([_http(404)], _tarball())
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    assert mod._download_doppler("9.9.9") is None
    assert fake.calls == 1, "a 404 was retried"
    assert mod.waits == []


def test_retries_are_bounded(mod, monkeypatch):
    """A blip that never clears gives up after the declared attempts."""
    fake = _Fake([_dns_blip()] * 10, b"{}")
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    assert mod.latest_release() is None
    assert fake.calls == len(mod._RETRY_DELAYS_S) + 1
    assert mod.waits == list(mod._RETRY_DELAYS_S)


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (_dns_blip(), True),
        (urllib.error.URLError(ConnectionRefusedError()), True),
        (urllib.error.URLError(TimeoutError()), True),
        (ConnectionResetError(), True),
        (TimeoutError(), True),
        (_http(500), True),
        (_http(503), True),
        (_http(404), False),
        (_http(403), False),
        (urllib.error.URLError("unknown url type: hxxp"), False),
        (ValueError("bad json"), False),
    ],
)
def test_transient_classification(mod, exc, transient):
    assert mod._transient(exc) is transient


def test_the_wall_clock_cap_is_not_retried(mod):
    """The download's overall cap bounds the retrying; it is not a blip."""
    assert not mod._transient(mod._DeadlineExceeded("cap"))


def test_a_retry_never_outlasts_the_cap(mod, monkeypatch, tmp_path):
    """A wait that would end past the download's cap is not taken."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        mod, "_platform_tag", lambda: ("linux-x86_64", ".tar.gz")
    )
    monkeypatch.setattr(mod, "_DOWNLOAD_DEADLINE_S", 1)
    fake = _Fake([_dns_blip()], _tarball())
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    assert mod._download_doppler("9.9.9") is None
    assert fake.calls == 1
