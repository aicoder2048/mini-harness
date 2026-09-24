# Project Instructions

A small, teaching-oriented Python coding agent (follow-along of Thorsten Ball's "How to Build an Agent").
Clarity beats features: keep changes small and easy to read.

## Commands

- Tests: `uv run pytest` (fast, never calls the real API; this is what CI runs)
- Live tests: `uv run --env-file .env pytest -m live` — real DeepSeek API, costs money, skipped by default.
  Run them after changing `providers.py`, `prompt.py`, tool descriptions or context pruning, or when asked.
- Lint / format check: `uvx ruff check src tests && uvx ruff format --check src tests`
- Auto-fix formatting: `uvx ruff format src tests`
- Use `uv` for everything Python; never `pip install`. Runtime dependencies are `openai` and `rich` (Markdown rendering) — no new ones without asking.
- Do NOT run the agent itself (`src/agent.py`, `src/step1_chat.py`) to verify a change: it calls the paid
  DeepSeek API and waits for interactive input. Verify with the tests above instead.

## Architecture

- `src/tools.py` — tools (`Tool` = name / description / input_schema / run). Knows nothing about any model API.
- `src/providers.py` — the only place that knows DeepSeek's wire format (OpenAI-compatible). Must stay swappable.
- `src/prompt.py` — `build_system_prompt(PromptContext)` is a pure function; I/O (git, AGENTS.md, memory index) lives in
  separate functions called from `main()`.
- `src/agent.py` — the loop. Depends on the three modules above; nothing depends on it.
- `src/` is on the pytest path, so tests import `agent`, `tools`, ... (not `src.agent`).

## Conventions

- Code comments, docstrings, README and commit messages are in Chinese; identifiers, tool descriptions and
  text sent to the model are in English.
- Tests use fakes (`FakeProvider`, `FakeOpenAI`, `tmp_path`) and test behavior through public interfaces.
- Write the test first, see it fail, then implement.
- Checks against the real API go into `tests/test_live.py` as `@pytest.mark.live` tests with behavior
  assertions (which tools were called, key facts in the answer) — never one-off scripts. Only pure
  exploration (e.g. measuring a token curve) may use a throwaway script; anything that should stay true
  becomes a live test.

## Lessons learned

- Every `tool_call` id the model sends must get exactly one `role=tool` reply, even when the call fails —
  never drop a call, or the next API request is rejected.
- DeepSeek thinking mode with tools: `reasoning_content` must be sent back in later requests
  (see `DeepSeekProvider._assistant_message`).
- Tool failures are returned to the model as error results; they must never crash the loop.
- Never commit `.env` (holds the API key); `.env.example` is the template.
- DeepSeek caches request prefixes automatically. Anything that changes early messages (editing the system
  prompt per turn, timestamps, pruning old messages every step) invalidates the cache from that point on.
  That's why pruning is batched: only when input exceeds `CONTEXT_BUDGET`, all at once.
- Pruning replaces old tool result *content* with a placeholder; never delete messages from `conversation`.
- `CONTEXT_BUDGET` must stay well above the size of the `KEEP_TOOL_RESULTS` kept results, or pruning
  fires every step. Keeping too few results makes the model re-read files it still needed.
- Memory notes are plain Markdown files in `Memory/` (the `MINI_HARNESS_MEMORY_DIR` default). The folder is
  gitignored and stays local. Never commit memory notes: deleting them later does not remove them from git history.
  Don't add a third-party memory tool (e.g. Basic Memory) unless the project actually needs it.
- `docs/*.pdf` are reference material — don't edit them.
