# EXPORT-CSV-v2: Task CSV export

## Overview

Status: planned (not implemented yet). Users of taskboard-api need to download the tasks of a
project as a CSV file for reporting in spreadsheets. This spec defines version 2 of the export
contract (version 1 was an informal script and is retired).

## Endpoint

`GET /projects/{id}/tasks/export.csv`

The route lives in the tasks router, the CSV rendering lives in the task service, following the
layering described in the architecture overview. The route must be declared before
`/projects/{project_id}/tasks/{task_id}` so that `export.csv` is not parsed as a task id.

## Columns

The CSV has exactly these columns, in this order: `id,title,status,assignee,created_at`.

- `id`: integer task id
- `title`: task title
- `status`: one of `todo`, `in_progress`, `done`
- `assignee`: assignee email address
- `created_at`: ISO 8601 UTC timestamp of task creation (requires adding `created_at` to the task model)

## Acceptance criteria

- AC-1: The response contains a header row with exactly `id,title,status,assignee,created_at`.
- AC-2: The response has `Content-Type: text/csv` (a `charset` parameter is allowed).
- AC-3: There is one data row per task of the project, ordered by ascending `id`.
- AC-4: Values containing commas, quotes or newlines are quoted per RFC 4180 (use the `csv` module).
- AC-5: A project without tasks returns 200 with the header row only.
- AC-6: If the project does not exist, the endpoint returns 404 as `application/problem+json`
  according to ADR-001.

## Out of scope

Filtering by status, streaming of very large exports and other formats (XLSX, JSON lines).
