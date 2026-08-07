# Skill: discover an agent API scenario

Use this procedure when the user wants an agent that performs business actions
but has no approved specification. Produce `docs/<scenario>/spec.md` from
`docs/templates/scenario-spec-template.md` before discussing Azure deployment.

## Facilitation

- Ask one opening question: **Who will use the agent, and what must they get
  done?**
- Draft a complete proposal from the answer. Ask at most five focused follow-up
  questions, one at a time.
- Offer concrete options and a recommended default instead of broad interviews.
- Mark unresolved facts as `[TO CLARIFY: question]`; never silently invent them.
- Record decisions in the specification and propagate them to affected stories.

## Cover these areas

1. **Persona and outcome**: one role, job-to-be-done, and measurable result.
2. **Work moments**: two or three recurring moments where the persona changes
   systems or loses time.
3. **Actions**: what the agent reads or writes. Each action is a candidate
   kebab-case `operationId` and MCP tool.
4. **Systems of record**: who owns each datum, whether the system is reachable,
   and whether it already provides a first-party MCP integration.
5. **Writes and guardrails**: explicit confirmation, human approval, forbidden
   actions, idempotency, and how a justified refusal offers an alternative.
6. **Demo storyline**: an annotated conversation where each user turn maps to a
   tool call and a specific example or `x-mock` response.
7. **Acceptance tests**: write verifiable statements such as: "WHEN the user
   asks X, THE AGENT SHALL call `tool-name` and cite Y."

Before inventing operations or schemas, run:

```bash
python tools/build-facade.py --catalog
python tools/build-facade.py --catalog schemas
```

Reuse compatible contracts and structures. A shared API contract is read-only;
create a variant when behavior or tool names must differ.

## Viability gate

Before presenting the specification, verify:

- every storyline action maps to a proposed tool;
- every response has realistic fictional example data;
- each required dynamic branch can be expressed by `x-mock`;
- writes require `Idempotency-Key` and explicit user confirmation;
- errors use RFC 7807-compatible `application/problem+json`;
- operation IDs use kebab-case;
- mocks do not promise state or business calculations;
- the test criteria can be exercised in the intended consumer.

If a created resource must later be checked, specify the corresponding read
operation now rather than leaving it for a future phase.

## Handoff

After user approval:

1. derive one OpenAPI contract per system of record under `apis/`;
2. put storyline data in response examples;
3. define deterministic `x-mock` selection rules;
4. create `clients/<id>/mcp-manifest.yaml`;
5. continue with `skills/onboarding.md`.
