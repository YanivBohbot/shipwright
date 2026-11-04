# ADR-001: Uniform error format (RFC 7807 problem+json)

## Status

Accepted and implemented in `app/errors.py`.

## Context

Clients of taskboard-api need one predictable error shape for every failure. FastAPI defaults
produce different bodies for HTTP errors (`{"detail": "..."}`) and validation errors (a list of
objects), which forces clients to special-case each status.

## Decision

All errors raised by the application use RFC 7807 problem details with the media type
`application/problem+json` and exactly these members:

```json
{"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Project 7 not found"}
```

- Services raise `AppError` subclasses (`NotFoundError` is 404, `ConflictError` is 409).
- Handlers registered by `register_error_handlers` convert `AppError`, request validation errors
  (422, `detail` joins `loc: msg` pairs with `; `) and Starlette HTTP exceptions.
- New error statuses (for example 429) must reuse the `problem()` helper.

## Consequences

- Clients parse a single shape and branch on `status`.
- Every new endpoint that can fail must raise `AppError` subclasses, never `HTTPException`
  with a custom body.
- `type` is always `about:blank` for now; dedicated problem type URIs may come later.
