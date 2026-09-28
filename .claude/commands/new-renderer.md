Implements a new renderer for the visual type $ARGUMENTS (skill `html-renderer`).

1. Confirm in `out/summary.md` how many instances of that `visualType` exist in the inventory; if there are few, ask me whether it's worth it before continuing.
2. Define the column contract in the `html-renderer` skill's table.
3. Implement `_opt_<kind>` in `src/pbix2html/render.py`, register it in `RENDERERS` and in `KIND_MAP`.
4. Add a fixture and a test in `tests/test_render.py`. Run `pytest -q`.
5. Render a sample HTML with `tests/fixtures/` and describe how it looks.
