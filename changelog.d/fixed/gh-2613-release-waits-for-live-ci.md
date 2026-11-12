- **A release waits while main's CI is still running** (gh-2613). The
    release's verify-ci job polled for a fixed 60 minutes, sized against
    CI's duration but counting its queue time too, so a release cut right
    after a code merge timed out with CI merely queued: v0.127.1 and both
    v0.128.0 attempts did, and each spent `make ship`'s one pre-publish
    recovery. The job now runs the org standard's `release-verify-ci`,
    which waits as long as the commit's CI run is alive, fails early only
    when no run appears or one finishes without certifying, and leaves the
    job's 240-minute timeout as the one cap. `release-watch` reads the
    same script, so the two no longer disagree about whether CI finished.
