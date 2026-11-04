# HEALTH-v1: Detailed health endpoint

## Overview

Status: planned. Today `GET /health` returns only `{"status": "ok"}`. Operators want a richer
payload to verify deployments and spot an empty or misconfigured instance.

## Response

`GET /health` returns 200 with a JSON object:

```json
{"status": "ok", "version": "0.1.0", "uptime_seconds": 12.5, "project_count": 2, "task_count": 7}
```

## Acceptance criteria

- AC-1: The response contains the keys `status`, `version`, `uptime_seconds`, `project_count`
  and `task_count`, and `status` is `"ok"`.
- AC-2: `version` is read from the installed package metadata (`importlib.metadata.version`)
  and not hard-coded.
- AC-3: `uptime_seconds` is a non-negative number measured from application start, using a
  monotonic clock.
- AC-4: `project_count` and `task_count` reflect the current content of the in-memory storage.
- AC-5: The endpoint stays unauthenticated, never fails because of empty storage and responds
  in constant time (no scans beyond `len()` of the storage dictionaries).

## Notes

Counting must go through the service layer rather than reading `app.storage` from the router.
