- **The scaffolded Python benchmark follows the constructor, like the
    test** (gh-1528). `src/<pkg>/benchmarks/bench_<comp>.py` constructs the
    object too, but it was written once and never again. So an init param
    added later left it calling the old constructor, and running it raised
    `TypeError`, with nothing reporting it. It is now born with the same
    `# jm:generated` line the test got in 0.88: while the line is there,
    `apply` keeps the file in step and `status --check` shows its drift.
    Delete the line to make it yours. A project scaffolded before this has
    no such line, and its benchmark stays untouched.
