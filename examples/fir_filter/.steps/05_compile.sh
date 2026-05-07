gcc -O2 -std=c99 -Inative/inc demo.c \
    build/native/src/fir_filter/libfir_filter_core.a \
    -lm -o demo && ./demo
