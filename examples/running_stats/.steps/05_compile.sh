gcc -O2 -std=c99 -Inative/inc demo.c \
    build/native/src/running_stats/librunning_stats_core.a \
    -lm -o demo && ./demo
