# Explicit extended endpoint verification

These are **data-plane acceptance checks**, not deployment, token acquisition,
Entra application creation, or proof of a live acceptance run. Use the installed
`mcp-kit` from the customer workspace (or its global `--workspace` option).
The operator separately approves the gateway, caller identity and fixture
effects. No Azure CLI, azd, management API or backend URL discovery is used when
the explicit endpoint flags below are supplied.

## Defaults and private authentication

Existing defaults are unchanged: `verify-rest` exercises only mock contracts;
native `verify-mcp` discovers tools without business calls; policy MCP verifies
its mock examples. External APIs are never treated as safe merely because an
operation is GET or has a `readOnlyHint` annotation.

```text
mcp-kit verify-rest clients/<id> --gateway-url https://<approved-gateway>
mcp-kit verify-mcp clients/<id> --gateway-url https://<approved-gateway> --profile native-mcp
```

Explicit mode requires `MCP_KEY` in the private process environment. Do not put
credentials in command arguments, transcripts, contracts, configuration files,
or screenshots. The verifier does not acquire credentials. Supply them using
your organization's approved private process-launch mechanism, and remove them
from that environment after use.

For a manifest declaring `inboundAuth.mode: entraJwt`, add
`--auth-mode entraJwt` or its alias `--auth-mode dual` and privately supply both
`MCP_KEY` and `MCP_BEARER_TOKEN`:

```text
mcp-kit verify-rest clients/<id> --gateway-url https://<approved-gateway> --auth-mode entraJwt
mcp-kit verify-mcp clients/<id> --gateway-url https://<approved-gateway> --profile native-mcp --auth-mode dual
```

**The current kit's `entraJwt` product requires a subscription key AND a JWT.**
It is not bearer-only. `dual` is a verifier flag alias, not a new manifest mode.
The flag must match the manifest; tenantId and audience must already be
configured. The gateway, not unverified local JWT decoding, validates the token.
A token's presence never silently upgrades subscription-key mode. Missing or
invalid private input and mismatched modes stop before any network request.
Neither credential is included in review hashes or output.

New flags require explicit endpoint mode; they cannot activate legacy azd or
pilot-key lookup. Bare legacy invocations remain compatible, but are not this
explicit, management-free path.

## Native MCP mock business examples

```text
mcp-kit verify-mcp clients/<id> --gateway-url https://<approved-gateway> --profile native-mcp --exercise-mock
```

All manifest backends must be mock. This opt-in reuses contract-derived,
model-reachable `x-mock` samples, **only for successful 2xx JSON examples**.
It verifies every selected native tool has such a case. With `both` exposure,
mock cases run on both applicable servers; real fixture calls below do not.
Only use this flag after confirming the deployed endpoints really are mocks:
a local manifest is not an attestation of the live deployment.

Before any business call, the verifier discovers all relevant servers, checks
exact tool-name sets, negotiates a supported protocol, retains session state and
validates every planned argument binding. Calls use the negotiated protocol
header and session ID. Supported body bindings are flat properties, `body`, and
`requestBody`; exactly one binding must satisfy the actual advertised input
schema. Ambiguous/unknown layouts fail closed, rather than dropping fields or
inventing arguments.

The assertions use the genuine MCP
[CallToolResult specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools):

- An object `structuredContent`, optionally accompanied by a human-readable
  summary or identical serialized JSON text.
- Otherwise exactly one JSON `TextContent` block.
- `isError` may be absent (false), but a tool error is a failure.
- If `outputSchema` is advertised, structured content is required and validated.

MCP JSON-RPC response IDs are checked, including multiline SSE messages and
interleaved notifications. The response must match the selected OpenAPI example
and its schema, in addition to any advertised output schema. Inputs are checked
against both contract constraints and the actual advertised input schema.
Native mock idempotency headers hidden by the transport may remain hidden;
controlled external fixture inputs are never dropped that way.

No assumption is made that APIM native MCP has the policy-MCP body shape.
An HTTP wrapper such as `{"statusCode":200,"body":...}`, non-JSON prose,
conflicting JSON/structured data, images, resource links or other unknown
formats fail explicitly. The verifier does not guess a nested body or follow
resource URLs. A provider-specific wrapper needs a separately verified adapter,
not a permissive extraction fallback.

MCP success proves the asserted **JSON payload**, not an underlying HTTP status.
Non-2xx mock branches are intentionally excluded; use REST verification for
HTTP status, media type and error examples. Supported contract schemas are
OpenAPI 3.0 with non-recursive local references, nullable conversion and
request/response readOnly/writeOnly required-field semantics. MCP schemas use
JSON Schema 2020-12. Remote schema references are never fetched.

## Controlled external fixtures: preview, review, authorize

For a real backend, select an exact allowlist of
`API/OPERATION/STATUS/EXAMPLE` entries using repeatable `--fixture` flags.
No values are supplied through CLI arguments. The same **named** example must
exist for each required parameter and required JSON request body. Optional
inputs are included only when that same named example exists. The selected
status/media response must have a named example and schema. There is no
fallback to unnamed examples, defaults, schema-generated identifiers, or
`externalValue`. Use a deliberately approved disposable dataset.

For example, a contract can supply these fragments on the same operation:

```yaml
parameters:
  - name: thingId
    in: path
    required: true
    schema: {type: string}
    examples:
      accepted-fixture: {value: T-DISPOSABLE-1}
responses:
  '200':
    description: Approved fixture
    content:
      application/json:
        schema:
          type: object
          required: [thingId]
          properties:
            thingId: {type: string}
        examples:
          accepted-fixture:
            value: {thingId: T-DISPOSABLE-1}
```

For a write, put the approved body under
`requestBody.content.application/json.examples.accepted-fixture.value`.
Include a named Idempotency-Key parameter example where the contract requires
one. Writes are **not** inferred safe: the operator must explicitly approve
the selected operation, data, caller, expected effect and cleanup beforehand.
MCP fixtures additionally require the operation in `mcpTools` and a successful
2xx response; use REST for controlled negative-response cases.

```text
mcp-kit verify-rest clients/<id> --gateway-url https://<approved-gateway> --fixture things/get-thing/200/accepted-fixture
mcp-kit verify-mcp clients/<id> --gateway-url https://<approved-gateway> --profile native-mcp --fixture things/get-thing/200/accepted-fixture
```

These commands emit one JSON preview with `mode: "fixture-preview"`,
`networkCalls: 0`, the gateway, effective auth mode, transport, exact fixture
IDs, methods, route templates, selected response statuses and `reviewToken`.
They need no credentials. Inputs and expected payloads are not printed: inspect
the named examples privately in the local contract before approval.

After that review, repeat **all** flags and selections with:

```text
--confirm-fixtures <reviewToken>
```

Add the same explicit `--auth-mode` on preview and invocation when using Entra.
The token binds the verifier implementation, gateway, effective auth mode,
complete manifest/contracts, compiled requests and expected assertions. Changed
code, origins, selections or inputs invalidate it. Credentials are intentionally excluded: this is a local
scope check, not cryptographic proof of human approval, caller identity or
unchanged live deployment. The operator remains responsible for approval and
live context. There is no `--unsafe`, implicit acceptance, or automatic retry.

Exactly one route is selected per controlled fixture: facade for `facade` or
`both`, otherwise its per-API route. This prevents duplicate real writes on
`both` exposure. REST sends only selected requests; MCP performs discovery only
on the servers serving selected fixtures, then invokes only those tools with
the selected inputs. Contract `servers` URLs and external backend URLs are not
used as destinations. Authentication/Host/transport header overrides are
rejected. Unsupported serialization and non-JSON bodies are rejected locally.

## Results, limits and failure handling

Successful invocations print one `[OK]` line per asserted call and a `RESULT`
line containing the actual call count and assertion scope. Native results
explicitly state that HTTP status/non-2xx branches were not asserted. Existing
default command output remains compatible. Exit 0 means successful preview or
all selected checks passed; exit 1 means validation/request/assertion failure;
argparse usage errors exit 2. A preview is never reported as executed acceptance.

Requests stay on the explicitly supplied HTTPS origin. Every redirect, even
same-origin, is refused without forwarding credentials. Responses are bounded
to 2 MiB; network operations use the existing 30-second socket timeout.
Error diagnostics expose classifications/status only, not response bodies,
provider messages, tokens, keys or request values. Unknown formats are failures,
not empty or success-shaped fallback results.

Controlled invocations stop at the first request/assertion failure. **Failure
does not roll back earlier calls and does not prove a timed-out write was not
executed.** Reconcile effects privately before retrying any real write.
Do not use a saved review token as permission to repeat an operation.
No tokens/apps are created, no local customer data is changed or deleted, and
no live native/external/Entra/Studio proof is claimed by this feature or its
offline tests.
