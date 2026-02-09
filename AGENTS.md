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

## UI Style Summary (app/ui.qss)

- **Color palette:**
	- Background: `#f3f4f6` (light gray)
	- Primary text: `#111827`
	- Muted text: `#6b7280`
	- Primary action: `#2563eb` (blue)
	- Accent / hover: `#1d4ed8`
	- Danger: `#ef4444` (hover `#dc2626`)
	- Borders: `#d1d5db` / `#e5e7eb`

- **Corner radii conventions:**
	- Buttons: ~10px (primary/ghost/danger)
	- Slightly larger elements (panels, reply blocks): ~12px
	- Cards / modals: 14–16px

- **Typography / spacing:**
	- Font: `Segoe UI`, default ~10pt
	- Buttons use bold / semi-bold for emphasis in dialogs
	- Padding is moderate (6–18px) depending on button size

- **Selectors & conventions:**
	- Styles target object names (ID selectors), e.g. `QPushButton#primaryButton`.
	- New UI elements should use existing object-name patterns and QSS IDs to match theme.
	- Use existing classes: `primaryButton`, `ghostButton`, `dangerButton` or add `deleteDialogButton`-style IDs when needed.

- **Design guidance:**
	- Prefer soft rounded corners and consistent padding.
	- Use `border` + `border-radius` together to ensure consistent rendering on Windows.
	- For modals use `OverlayController` and keep modal sizes aligned with other overlays.

