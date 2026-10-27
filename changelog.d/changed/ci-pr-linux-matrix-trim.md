- CI: a pull request now runs ubuntu-latest at its oldest, middle and
    newest Python and ubuntu-24.04-arm at its oldest and newest, as it
    already did for macOS; the push that lands it runs the rest, so `main`
    still certifies the whole matrix.
