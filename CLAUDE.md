Refer to projects when needed in [references directory](./llm/references/)

For coding taste refer to [Taste.md](./llm/Taste.md)

Run the frappe-dev skill before for Frappe development tasks

The spec lives in [spec/](./spec/README.md) and is the source of truth.
Read [spec/README.md](./spec/README.md) before touching anything in this
app.

## Formatting

`ruff` (config in [pyproject.toml](./pyproject.toml): tab indent, double
quotes, line-length 110) runs via the `ruff-format` pre-commit hook. The
existing code was formatted with an older ruff, so the current formatter
rewrites pre-existing lines it didn't touch — do **not** commit those
whole-file reformats. Keep diffs to the lines you actually changed:

- Match the surrounding file's existing style when writing new code.
- Don't run `ruff format` across whole files or the repo to "clean up."
- If the hook reformats unrelated lines, `git checkout` them and stage
  only your intended changes (e.g. `git add -p`).