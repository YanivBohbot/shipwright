# taskboard-api architecture overview

## Layering

The application is split in three layers and dependencies only point downward:

1. Routers (`app/routers/`): HTTP concerns only. Parse and validate input with pydantic models,
   call one service function, shape the response and status code.
2. Services (`app/services/`): business logic and rules (existence checks, cascading deletes,
   pagination). Services raise `AppError` subclasses (ADR-001) and never import FastAPI.
3. Storage (`app/storage.py`): in-memory dictionaries and id counters. Process-local, data is
   lost on restart.

Shared modules: `app/models.py` (pydantic models) and `app/errors.py` (problem+json handlers).
`app/main.py` wires routers and error handlers.

## Conventions

- Routers never touch `app.storage` directly; go through a service.
- Errors follow ADR-001 (RFC 7807 problem+json).
- Lists are paginated with `limit` (1-100, default 20) and `offset`.

## Testing conventions

- Tests live in `tests/` and use pytest with FastAPI `TestClient`.
- `tests/conftest.py` resets storage around every test (autouse) and provides `client` and
  `project` fixtures.
- Test names describe behavior, for example `test_create_task_with_unknown_project_returns_404`.
- Each new endpoint needs tests for the happy path, the 404 case and validation errors.
- Run the suite with `uv run pytest -q`. Known bugs are marked `xfail(strict=True)`.
