- **jm owns the packaging templates, so a pkg-config or `find_package` fix
    reaches an existing project** (gh-1589, part 1). `cmake/<pkg>.pc.in` and
    `cmake/<pkg>-config.cmake.in` hold no authored content, but they were
    create-only: every packaging fix reached new projects only. They are now
    born carrying jm's ownership token, and `jm apply` renders them whole while
    it is there; deleting the `# jm:generated` line hands a template to you for
    good. A project scaffolded earlier: `jm status` lists each template that is
    behind under a new **PACKAGING** section (advisory, never counted, also in
    `--json`), and `jm adopt --packaging [--check] [--accept PATH]` hands them
    to jm, refusing one whose adoption would drop a line today's render does
    not keep. `jm adopt --help` now prints its usage (gh-1569).
