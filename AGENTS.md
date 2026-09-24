# Project Instructions

A small, teaching-oriented Python coding agent (follow-along of Thorsten Ball's "How to Build an Agent").
Clarity beats features: keep changes small and easy to read.

## Commands

- Tests: `uv run pytest` (fast, never calls the real API)
- Lint / format check: `uvx ruff check src tests && uvx ruff format --check src tests`
- Auto-fix formatting: `uvx ruff format src tests`
- Use `uv` for everything Python; never `pip install`. Runtime dependencies are `openai` and `rich` (Markdown rendering) — no new ones without asking.
- Do NOT run the agent itself (`src/agent.py`, `src/step1_chat.py`) to verify a change: it calls the paid
  DeepSeek API and waits for interactive input. Verify with the tests above instead.

## Architecture

- `src/tools.py` — tools (`Tool` = name / description / input_schema / run). Knows nothing about any model API.
- `src/providers.py` — the only place that knows DeepSeek's wire format (OpenAI-compatible). Must stay swappable.
- `src/prompt.py` — `build_system_prompt(PromptContext)` is a pure function; I/O (git, AGENTS.md) lives in
  separate functions called from `main()`.
- `src/agent.py` — the loop. Depends on the three modules above; nothing depends on it.
- `src/` is on the pytest path, so tests import `agent`, `tools`, ... (not `src.agent`).

## Conventions

- Code comments, docstrings, README and commit messages are in Chinese; identifiers, tool descriptions and
  text sent to the model are in English.
- Tests use fakes (`FakeProvider`, `FakeOpenAI`, `tmp_path`) and test behavior through public interfaces.
- Write the test first, see it fail, then implement.

## Lessons learned

- Every `tool_call` id the model sends must get exactly one `role=tool` reply, even when the call fails —
  never drop a call, or the next API request is rejected.
- DeepSeek thinking mode with tools: `reasoning_content` must be sent back in later requests
  (see `DeepSeekProvider._assistant_message`).
- Tool failures are returned to the model as error results; they must never crash the loop.
- Never commit `.env` (holds the API key); `.env.example` is the template.
- `docs/*.pdf` are reference material — don't edit them.
