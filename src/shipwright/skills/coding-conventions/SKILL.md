---
name: coding-conventions
description: Code style and architecture rules for the FastAPI taskboard-api repo (routers/services/models layering, Pydantic, ADR-001 problem+json errors, type hints). Use when writing or reviewing Python code in the target repository.
---

# Coding conventions (FastAPI repo)

Audience: the **developer** (follow these rules while coding) and the **reviewer** (check the diff
against them). Organisation-wide rules live in `/policies/POLICIES.md` and the error format in
ADR-001; this skill points to them and does not replace them. If anything here conflicts with the
policies, the policies win.

## Layering

Dependencies flow one way: `routers -> services -> storage`.

- `app/routers/` is the HTTP layer only: declare the route, parse and validate input through
  Pydantic models, call exactly one service function, return its result. **No business logic,
  no storage access, no branching on domain rules in a router.**
- `app/services/` holds business logic. Services raise domain errors and never import FastAPI
  request or response objects.
- `app/storage.py` is the in-memory store. Only services touch it.
- `app/models.py` holds the Pydantic models (create, update, read, page). Add new request and
  response shapes there.
- Put each new feature in the existing layer that fits; add a new module only when the existing
  ones clearly do not fit, and mirror the naming of neighbouring files.

## Models and validation

- Use Pydantic models for every request body and response; do not accept raw `dict`.
- Express constraints in the model (`Field(min_length=1)`, validators, enums such as
  `TaskStatus`) so invalid input becomes a 422 before it reaches a service.
- Keep create, update and read models separate. Reuse the generic `Page[...]` model for lists.
- List endpoints take `limit` (1-100) and `offset` query parameters, as existing ones do.

## Errors (ADR-001)

- Services raise `AppError` subclasses from `app/errors.py` (`NotFoundError` -> 404,
  `ConflictError` -> 409). For a new status, add a subclass; if you must build a response
  directly, use the `problem()` helper.
- **Never** raise `HTTPException` with a custom body and never return ad-hoc error JSON.
- Every error response is `application/problem+json` with exactly `type`, `title`, `status`,
  `detail`. Messages are short and specific (`Project 7 not found`).
- Do not catch broad exceptions to hide failures; an unexpected crash is a bug to fix at its
  cause, not to mask.

## Style

- Type hints on every function signature, including return types. Use modern syntax
  (`list[str]`, `X | None`).
- Match the surrounding code: naming (`snake_case` functions, `PascalCase` classes), import
  order, small focused functions, no dead code, no debugging prints.
- Keep changes minimal and focused on the ticket. No unrelated refactors or reformatting of
  files you did not otherwise need to touch.
- Comments explain why, not what. Reflect public behaviour changes in `README.md` and
  `CHANGELOG.md` when the repository keeps them for that purpose.
- Never put credentials in code, tests or docs (policy).

## Reviewer checklist

When reviewing, flag each violation with file and line:

1. Logic or storage access inside a router.
2. Error paths that bypass `AppError` / ADR-001 (`HTTPException`, hand-built JSON).
3. Missing type hints or untyped request bodies.
4. Unrelated changes, dead code, or diff size above the policy limit.
5. Behaviour change without tests (see the `testing-guidelines` skill).
