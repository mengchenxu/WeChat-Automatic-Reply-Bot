# Agent Brief Template

A good agent brief is a self-contained spec an AFK agent can implement without reading any other context.

## Structure

```markdown
## What to build

A concise description of this vertical slice. Describe the end-to-end behavior,
not layer-by-layer implementation. Use the project's domain vocabulary
(from CONTEXT.md).

### Key rules

- rule 1
- rule 2

## Acceptance criteria

- [ ] Criterion 1 — specific, testable
- [ ] Criterion 2 — specific, testable

## Blocked by

- #issue-number

Or "None — can start immediately."

## Parent

- PRD: path/to/prd.md
```

## Durable briefs

- **State what, not how** — describe what the user sees, not which files to edit.
- **Use domain vocabulary** — `mention_name`, `aliases`, `Store`, `Pipeline` etc. from CONTEXT.md.
- **Testable acceptance criteria** — every checkbox should be verifiable without reading the code.
- **No file paths or code snippets** — they go stale faster than prose descriptions.
- **Reference parent docs** — link the PRD/spec that spawned this issue.
- **One vertical slice** — a brief that touches parse + store + send is fine; three briefs that each touch one layer are not.

## When the issue is ready

Apply `ready-for-agent` label and post the brief as the issue body.
