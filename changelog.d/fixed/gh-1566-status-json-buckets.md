- **`jm status --json` carries the UNRECONCILED buckets** (gh-1566). A
    new `unreconciled` key lists each fragment under `actionable`,
    `binding_ahead`, `apply_fixes` or `unexplained`, with its per-member
    reasons and the members whose binding is ahead of the manifest. Before,
    a downstream automating on `--json` could not tell a fragment that is
    safe to re-render from one whose re-render would delete a working
    binding. Both faces now read one classifier.
