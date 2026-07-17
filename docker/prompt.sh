# just-makeit example sandbox — interactive shell prompt.
#
# Installed as /etc/profile.d/05-jm-prompt.sh (sourced by the login shell,
# which is the container's CMD, *after* /etc/bash.bashrc so it wins) and also
# appended to /etc/bash.bashrc (for a nested non-login `bash`). The host part
# is hard-coded rather than `\h` (which would show the container-ID hash) and
# the user part rather than `\u` (which shows "I have no name!" when the
# container runs as a bare numeric UID with no /etc/passwd entry — e.g. under a
# UID remap). The result is a stable `user@jm-sandbox:<cwd>$` regardless of
# runtime UID.
PS1='\[\033[1;32m\]user@jm-sandbox\[\033[0m\]:\[\033[1;34m\]\w\[\033[0m\]\$ '
