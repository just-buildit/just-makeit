- **The Visual Studio example is two: `visual_studio_pip` and
    `visual_studio_uv`** (gh-2610). `jm example visual_studio` is gone. Its
    venv step gave two routes at once -- `py -m venv` plus pip, or "with uv,
    `uv venv` then `uv pip install numpy`" -- and a user who mixed them ended
    in a uv venv, which has no pip, running a pip line in it. Each page now
    gives one route: `py -m venv .venv` and then
    `.venv\Scripts\python -m pip install numpy`, or `uv sync`, which makes
    `.venv` with numpy and the project installed. Both say to make the venv before opening the folder
    in Visual Studio, and what the configure says if it was opened first.
    The Windows clang-cl CI job runs each page's venv lines as written,
    downloads included; elsewhere the test replaces them with an offline
    venv and says which lines it replaced, also under any failure. The
    pages' shared steps are held byte-identical by a test, and
    `docs/windows.md` gives the same two routes.
