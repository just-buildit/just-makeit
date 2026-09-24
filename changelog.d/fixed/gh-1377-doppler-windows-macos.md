- **The `nco_tone` and `kitchen_sink` examples now build against doppler on
    macOS and Windows** (gh-1377). The example asked for doppler's macOS
    build as `darwin-arm64`, a name doppler has never published (it is
    `macos-arm64`), and had no Windows entry at all -- so on both the fetch
    came back empty, the build step was skipped, and the test still reported
    PASSED. It now reads doppler's real asset names, unpacks the Windows
    `.zip`, and falls back to doppler 0.55.0 when the release list cannot be
    reached. Set `JM_REQUIRE_DOPPLER=1` to make an unavailable doppler a
    failure rather than a skip; jm's CI sets it on every leg that runs the
    examples, and no longer deselects the two on Windows.
