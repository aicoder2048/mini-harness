---
name: run-checks
description: Run this repository's checks — the fast pytest suite, ruff lint and ruff format check — and report exactly what passed, what failed and what was not run. Use after changing code here, before committing, or whenever the user asks whether the project is healthy, to verify a change, or to "run the tests", even if they don't mention ruff or pytest by name.
---

# Run checks

An example skill that ships with mini-harness. It is small on purpose: the point is to show how a skill is found (the `# Skills` index), loaded (read this file) and followed.

## Steps

Run these from the repository root, in this order, and keep going even if one fails so the report is complete:

1. `uv run pytest -q` — the fast suite. It uses fakes and never calls the real API.
2. `uvx ruff@0.16.9 check src tests` — lint.
3. `uvx ruff@0.16.9 format --check src tests` — formatting.

Use the pinned ruff version: it is the one CI uses, and a newer ruff can add default rules that fail for reasons unrelated to the change.

Don't run the live tests (`pytest -m live`) unless the user asks: they call the paid DeepSeek API.

## Report

One line per check with the actual result, for example:

- pytest: 201 passed, 10 deselected
- ruff check: all checks passed
- ruff format: 1 file would be reformatted (`src/agent.py`)

Quote the real numbers from the output. If a check could not run (the user denied the command, `uv` is missing), say so instead of guessing. Don't summarize partial results as "everything passes". If something failed, show the relevant lines of output and offer to fix it.
