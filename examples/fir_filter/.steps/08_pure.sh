cd ..
just-makeit new my_fir_pure \
    --component fir_pure \
    --pure \
    --param "coeffs:float[16]" \
    --param "delay:float _Complex[16]"
cd my_fir_pure
