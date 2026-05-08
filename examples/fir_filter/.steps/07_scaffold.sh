# run from the parent of my_fir
just-makeit new my_fir_perf \
    --component fir_filter \
    --state "coeffs:float[16]" \
    --state "delay:float _Complex[16]" \
    --state "gain:float:1.0" \
    --perf
cd my_fir_perf
