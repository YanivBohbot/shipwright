# ADR-002: Rate limiting

## Status

Proposed (not implemented yet).

## Context

taskboard-api is public and has no authentication. A single noisy client can degrade the service
for everyone. The service runs as a single process with in-memory storage, so any limiter state
can also live in process.

## Decision

Add an in-process rate limiter as ASGI middleware:

- Limit: 60 requests per minute per client IP (sliding or fixed one-minute window).
- Over the limit the service answers 429 as `application/problem+json` following ADR-001
  (`title` "Too Many Requests") and sets a `Retry-After` header with the number of seconds
  until the client may retry.
- `GET /health` is exempt so that monitors are never blocked.
- The client IP is `request.client.host`; proxy headers are not trusted.
- The limit is configurable through a setting, default 60.

Alternatives considered:

- Redis-backed limiter (shared state across instances): rejected for now, the service is
  single-process and adding Redis is disproportionate.
- Third-party library such as slowapi: rejected to avoid a dependency for a small feature.
- Rate limiting at a reverse proxy or API gateway: valid long term, but the demo is deployed
  without one.

## Consequences

- Counters are lost on restart and are not shared between processes; with several workers the
  effective limit is multiplied by the number of workers.
- Tests need a way to reset limiter state between cases.
- Clients must honor `Retry-After`.
