- **`jm upgrade` no longer says `apply` will refuse an unknown key**
    (gh-1702). At a current schema, a manifest key this jm does not read was
    reported as "Not up to date: `just-makeit apply` will refuse until these
    are resolved", while `apply` warns on it and exits 0. `upgrade` now lists
    the key as unread and advisory. A declaration `apply` does refuse, such
    as `error` left with no `status_return`, is refused by the same manifest
    load in both commands, with the same message, and a test runs both
    commands over each shape to hold their verdicts equal.
