# AGENTS.md

## Purpose
Instructions for Codex when working in this repo.

## Priorities
- Make the smallest change that solves the user request.
- Keep behavior stable unless explicitly asked to change it.
- Prefer clarity over cleverness.
- Test all changes you introduce.
- Ask clarifying questions when requirements conflict or don’t line up.

## Conventions
- Language: Python (PySide6 UI in `app/client.py`, server in `server/server.py`).
- Use `rg` for search.
- Prefer `apply_patch` for small, single-file edits.
- Keep new code ASCII unless the file already uses non-ASCII.

## Tests
- If you add logic, run the smallest relevant check if available.
- If you did not run tests, say so.

## Safety
- Never modify secrets or tokens unless explicitly asked.
- Confirm destructive actions before proceeding.

## Files To Know
- `app/client.py` — UI client.
- `server/server.py` — server.
- `server/tokens.json` — tokens (read-only unless asked).

## Questions To Ask When Unclear
- Desired behavior and edge cases.
- OS-specific expectations (Windows path handling, file opening).
