- **jm's installers no longer make a partial upgrade on Arch.** Both
    `install.sh` and `jm-install-deps` refreshed pacman's databases without
    upgrading (`-Sy`) and then installed, which Arch does not support: a
    package from the fresh database can need libraries newer than the ones
    installed beside it, as openssl 4 did against `libcrypto.so.3` on
    2026-11-11. They now run `-Syu --noconfirm --needed`, and a test refuses
    a refresh-without-upgrade in any tracked file.
