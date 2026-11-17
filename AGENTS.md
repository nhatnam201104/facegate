# AGENTS.md

## Repository Structure

- `server/` — Python FastAPI backend & AI face-attendance pipeline (structured like `msoftware-msw-chat-mm-server/server/`):
  - `api/` — HTTP/WebSocket route handlers & shared runtime state (`records.py`, `state.py`)
  - `vision/` — detection/embedding engines, face quality, IoU tracking, pipeline, camera, liveness, clips, overlay drawing
  - `enrollment/` — RAM embedding gallery, guided 5-angle camera enrollment, video enrollment
  - `attendance/` — shift/late calculation and HRM outbox adapters (`MockHRM`, `ERPNextHRM`)
  - `store/` — SQLite persistence & migrations (`db.py`)
  - `models/` — ONNX weights and 3DDFA coefficient binaries
  - `scripts/` — Model download (`download_models.py`) and backend benchmark (`benchmark.py`)
  - `tests/` — Unit tests (`pytest`)
- `webapp/` — Pure HTML/CSS/JS frontend dashboard served at `/` and `/static`

## Pull Requests

When creating a pull request, follow `.github/PULL_REQUEST_TEMPLATE.md`:

- Remove all `<!-- -->` comments.
- Omit sections that are not applicable (`Ticket Link`, `Screenshots`) — do not write `N/A`, just remove the header.
- The `#### Release Note` header and its `release-note` fenced code block **must always be present**. Write `NONE` if the change has no API, config, schema, UI, or breaking changes.
- Store the generated PR description in `.agent/pr-description.md` (gitignored).
