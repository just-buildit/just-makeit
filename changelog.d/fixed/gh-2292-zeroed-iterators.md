- **A composer with a `realtime` clock no longer crashes when its
    `stream()` iterator is freed** (gh-2292). This was a regression in
    0.105.1 and 0.105.2. The iterator was allocated without zeroing,
    and gh-2272's in-flight lock was garbage, so dropping the iterator
    aborted the process. Every iterator jm generates is now allocated
    zeroed, and a gate refuses any generator that allocates without
    zeroing. Projects on 0.105.1 or 0.105.2 with a composer
    `[module.X.composer.realtime]` table should upgrade.
