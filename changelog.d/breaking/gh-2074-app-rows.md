- **`[app]` is now `[[app]]`, one row per app, and an app's name is unique
    in the project** (gh-2074, schema 9). `jm upgrade` rewrites a schema-8
    `[app]` table in place as a one-row `[[app]]` -- only the header line
    changes -- and until then `jm apply`, `jm status` and `jm app` refuse the
    old spelling, naming `jm upgrade`. A `jm app` whose name another app
    holds is refused, naming `jm remove app <name>`, where it used to replace
    that app; one whose DEFAULT name is taken (the project's, or the
    function's) names `--name` too. So three faces over one core take three
    names, and a second console app over one package is refused, since both
    would write its `cli.py`. See docs/upgrading.md.
