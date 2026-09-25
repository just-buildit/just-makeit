- **`jm adopt --packaging` replays a project the way `apply` does**
    (gh-1589). It called the manifest replay bare, outside the scopes
    `apply` runs it in, so on a project whose module references a component
    by `init_param object = ...` before that component's capsule property is
    replayed -- doppler -- it refused with "publishes no capsule" and did
    nothing. Both now go through one `replay_project`, and a test refuses
    any other caller of the replay.
