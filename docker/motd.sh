#!/bin/sh
# Printed once per interactive shell session.
cat <<'MSG'

  just-makeit example sandbox (Linux)
  ====================================

  Pre-built projects in ~/examples/:

    my_fir/         FIR filter — single object, state vars, perf annotations
    my_stats/       Running mean + variance over a sliding window
    my_corr/        Sliding cross-correlator (batch method, variable output)
    my_power/       Sliding power estimator
    my_arrays/      Array-typed inputs and multi-output methods
    my_chunker/     Stream rechunker (stateless object)
    my_filters/     Multiple objects compiled into one .so (filter module)
    dsp_toolkit/    Multi-module project with init params
    iqfile/         IQ file reader with Python properties

  Browse a project:
    cd ~/examples/my_fir && ls

  Read the tutorial README:
    python3 -m just_makeit._example_readme fir_filter | less
    # or: find / -path "*/examples/fir_filter/README.md" 2>/dev/null | xargs less

  Re-run an example end-to-end (scaffolds fresh in /tmp):
    just-makeit example fir_filter

  Start your own project:
    cd ~ && just-makeit new my_proj --object my_obj
    cd my_proj
    # edit src/my_proj/my_obj.c, then:
    make && python3 -c "import my_proj; print(my_proj.MyObj())"

MSG
