# Printed once per interactive PowerShell session.
Write-Host @"

  just-makeit example sandbox (Windows / MinGW)
  ===============================================

  Pre-built projects in $HOME\examples\:

    my_fir\         FIR filter -- single object, state vars, perf annotations
    my_stats\       Running mean + variance over a sliding window
    my_corr\        Sliding cross-correlator (batch method, variable output)
    my_power\       Sliding power estimator
    my_arrays\      Array-typed inputs and multi-output methods
    my_chunker\     Stream rechunker (stateless object)
    my_filters\     Multiple objects compiled into one .pyd (filter module)
    dsp_toolkit\    Multi-module project with init params
    iqfile\         IQ file reader with Python properties

  Browse a project:
    cd `$HOME\examples\my_fir; dir

  Re-run an example end-to-end (scaffolds fresh in TEMP):
    just-makeit example fir_filter

  Start your own project:
    cd `$HOME
    just-makeit new my_proj --object my_obj
    cd my_proj
    # edit src\my_proj\my_obj.c, then:
    cmake -B build -G 'MinGW Makefiles'
    cmake --build build
    python -c "import my_proj; print(my_proj.MyObj())"

"@ -ForegroundColor Cyan
