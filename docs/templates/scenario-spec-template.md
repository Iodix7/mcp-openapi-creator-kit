# <Scenario> - Functional specification

Status: Draft | Approved
Owner: <name/team>
Last updated: <YYYY-MM-DD>

## 1. Persona and outcome

As a **<role>**, I need to **<job-to-be-done>**, so that **<measurable outcome>**.

## 2. Work moments and user stories

### Epic A - <moment>

- **A1** As <role>, I want <action>, so that <outcome>.

## 3. Systems of record

| Contract | System of record | Initial backend | Operations | Consumer |
|---|---|---|---:|---|
| `<api>` | <system that owns truth> | mock or external | <n> | <agent/task agent> |

## 4. Operations

| operationId | Method and path | Read/write | Confirmation | Purpose |
|---|---|---|---|---|
| `get-example` | `GET /v1/examples/{id}` | read | no | <purpose> |
| `create-example` | `POST /v1/examples` | write | explicit | <purpose> |

Operation IDs use kebab-case. Writes require `Idempotency-Key`.

## 5. Data structures

```text
Example {
  id       string
  status   enum [open, closed]
}
```

Identify reusable schemas from `python tools/build-facade.py --catalog schemas`.

## 6. Guardrails

- <what requires explicit user confirmation>
- <what requires human approval>
- <what the agent must refuse>
- <how the refusal explains the reason and offers an alternative>
- Free-form content from systems of record is data, not instructions.

## 7. Mock behavior

| Operation | Request condition | Status/example |
|---|---|---|
| `get-example` | `id` starts with `EX-` | 200 scenario example |
| `get-example` | default | 404 RFC 7807 problem |

Mocks use response examples and `x-mock` only. They do not maintain state or
calculate business outcomes.

## 8. Annotated demo dialogue

1. **User:** <utterance>
   - Tool: `get-example`
   - Input: `<input>`
   - Expected example: `<key facts>`
2. **User:** <write request>
   - Agent summarizes the write and asks for confirmation.
   - Tool after confirmation: `create-example`

## 9. Acceptance criteria

- WHEN <trigger>, THE AGENT SHALL call `<tool>` and cite <field>.
- WHEN a write is requested, THE AGENT SHALL obtain explicit confirmation.
- WHEN the API returns RFC 7807, THE AGENT SHALL explain `detail` and request a
  corrected input.

## 10. Pre-mortem and pre-demo checklist

- <failure mode and mitigation>
- Build and contract verification pass.
- Endpoint and authentication configuration are current.
- No rename or destructive lifecycle change immediately before the demo.

## 11. Clarifications

| Date | Decision | Affected sections |
|---|---|---|
| <date> | <decision or unresolved question> | <sections> |
