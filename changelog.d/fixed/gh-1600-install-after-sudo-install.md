- **An install after a `sudo cmake --install` no longer fails on the `.pc`**
    (found by gh-1600's consumer smoke). The install step writes each
    library's configured `.pc` into the build tree, and a root install left
    that file owned by root, so the next install as the user -- a DESTDIR
    stage, a second prefix, `--component runtime|dev` -- failed with
    `file failed to open for writing (Permission denied)`. The file is now
    removed before it is written.
