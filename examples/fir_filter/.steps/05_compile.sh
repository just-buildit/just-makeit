gcc -O2 -std=c99 -Inative/inc demo.c \
    -Lbuild -lmy_fir -Wl,-rpath,build \
    -lm -o demo && ./demo
