gcc -O2 -std=c99 -Inative/inc demo.c \
    -Lbuild -lmy_stats -Wl,-rpath,build \
    -lm -o demo && ./demo
