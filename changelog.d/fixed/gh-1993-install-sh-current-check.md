- **The curl installer sees an up-to-date just-makeit on macOS and Alpine**
    (gh-1993). `install.sh` read the latest version off
    `pip index versions` with `grep -oP`, which only GNU grep has. On macOS
    (BSD grep) and Alpine (busybox grep) the version came back empty, so
    just-makeit never counted as current: `--check` always exited 1, and
    every run reinstalled it. The version is now read with POSIX `sed`.
