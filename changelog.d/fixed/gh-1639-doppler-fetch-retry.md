- **The `nco_tone` example survives a network blip** (gh-1639). It fetched
    doppler with a single attempt, so one DNS failure on one CI runner failed
    a release leg. The release lookup and the download now retry a transient
    error (name resolution, a refused or reset connection, a timeout, an HTTP
    5xx) up to three attempts, within the download's existing time cap. A 404
    still fails at once. `nco_tone_ci.yml` now runs that same fetch instead
    of its own `gh release download`, which had no retry either.
