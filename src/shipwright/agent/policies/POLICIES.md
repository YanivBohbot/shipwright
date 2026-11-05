# Shipwright organisation policies

These rules are read-only and apply to every pull request Shipwright prepares.

## Forbidden paths

- Never modify `.github/workflows/**`.
- Never modify dependencies in `pyproject.toml` without an explicit justification in the PR description.

## Pull request size

- A pull request must contain at most 400 changed diff lines.
- If the change would exceed 400 lines, stop and propose a split into smaller pull requests instead of publishing.

## Safety

- Never put credentials (tokens, passwords, API keys) in code, tests, commits or PR text.

## Testing

- Every behaviour change must come with tests.

## Error format (ADR-001)

- Report failures using the ADR-001 error format: a stable error code, a short human-readable message, and the actionable next step.
