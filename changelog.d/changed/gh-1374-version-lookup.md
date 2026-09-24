- **The running version is looked up once per process** (gh-1374).
    `jm_cli_version()`, which every mutating command uses to stamp the
    manifest, called `importlib.metadata.version` afresh each time. It now
    reads `just_makeit.__version__`, which resolves once and caches a
    success. Where the package has no metadata, every lookup failed and
    was retried. On Python 3.9, whose `importlib.metadata` has no path
    cache, each retry scanned all of `sys.path`, and that made jm's own
    3.9 test leg run about three times as long as 3.12's.
