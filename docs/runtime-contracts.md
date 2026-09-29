# Opt-in REST interface-agreement runtime

The kit does **not** interpret arbitrary natural-language interface agreements.
OpenAPI schemas, response examples and `x-mock` remain the source of truth.
Descriptions alone do not activate validation, authorization, response headers,
UUID generation, or error mappings.

`x-kit-runtime` version 1 adds a bounded declarative implementation for
**one GET operation per API**, with a credential-free mock backend, `perApi`
exposure, `targets.consumer: rest`, and the **`rest-consumption`** profile.
It is useful for deterministic interface-agreement demonstrations, not business
state, persistence, backend forwarding or production availability guarantees.
APIs without the opt-in keep their existing behavior.

## Contract declaration

This is a fragment of a **synthetic** agreement, not a complete OpenAPI file.
Add the following at the document root:

```yaml
security:
  - clientKey: []
components:
  securitySchemes:
    clientKey:
      type: apiKey
      in: header
      name: x-api-key
x-kit-runtime:
  version: 1
  securityScheme: clientKey
  correlation:
    header: X-Correlation-Id
    bodyProperty: correlationId
  errors:
    badRequest:   {status: 400}
    unauthorized: {status: 401}
    forbidden:    {status: 403}
    rateLimited:  {status: 429}
```

Each error selector accepts optional `example: <name>`. Its status is fixed to
the value shown. An explicit selector is required if the response has multiple
named examples. No inline message, policy, XML, expression, or code is accepted.
Error bodies are the selected examples, with **only** the correlation property
replaced; the compiler does not invent problem codes or rewrite `detail`.

For example, a contract can declare `GET /v1/things/{thingId}/status` with:

```yaml
parameters:
  - name: thingId
    in: path
    required: true
    description: Case-sensitive identifier.
    schema:
      type: string
      minLength: 1
      maxLength: 64
      pattern: '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'
  - name: X-Correlation-Id
    in: header
    required: true
    description: Canonical hyphenated UUID, echoed exactly.
    schema: {type: string, format: uuid}
x-mock:
  - when: {param: thingId, equals: T-1, caseSensitive: true}
    respond: {status: 200, example: first}
  - when: {param: thingId, equals: T-2, caseSensitive: true}
    respond: {status: 200, example: second}
  - when: {param: thingId, equals: T-3, caseSensitive: true}
    respond: {status: 200, example: third}
  - respond: {status: 404}
```

Version 1 validates declared path/query/header strings, required presence,
`minLength`, `maxLength`, string enums and `format: uuid`. Supported patterns
are anchored concatenations of ASCII literals/character classes with bounded
`{n}`/`{min,max}` quantifiers, with at most 512 matched characters. Arbitrary
.NET regular expressions, unbounded quantifiers, lookarounds, unsupported input
types/formats/constraints and custom parameter serialization fail validation.
UUIDs require exactly 8-4-4-4-12 hexadecimal characters with hyphens; uppercase,
lowercase, nil and non-v4 values are accepted. No trimming or recasing occurs.
Generated regexes use the absolute .NET `\z` end anchor, not `$`, so a final
newline does not satisfy the pattern. Header-name lookup uses APIM's
case-insensitive header dictionary; header values and opt-in case-sensitive
lookup values are not normalized. Duplicate scalar header/query values exposed
by that dictionary are rejected. Duplicate correlation values produce a new
UUID; duplicate subscription header values are rejected as authentication
failures. Gateway normalization before policy execution still needs acceptance.
Declared parameter names must be unique across locations and casing in v1
(path/operation parameter overrides are not supported). Response header names
must likewise be unique case-insensitively.

Every response must declare:

- An object JSON example and a required top-level string `correlationId`
  (or the configured body property), without a fixed enum or extra restrictions.
- The configured correlation response header with `type: string, format: uuid`.
- Exactly `application/json` for success, `application/problem+json` for errors.
- Any static response headers, with unambiguous literal header/schema `example`
  values or a singleton schema `enum`. Descriptions are not executable rules.

For all-results no-store behavior, include this header on **each** response:

```yaml
headers:
  X-Correlation-Id:
    schema: {type: string, format: uuid}
  Cache-Control:
    schema: {type: string, enum: [no-store]}
```

The 429 response additionally needs `Retry-After`:

```yaml
Retry-After:
  schema: {type: integer, minimum: 1}
  example: 5
```

Do not fix its schema to `enum: [5]`: actual throttling uses the gateway's
recommended remaining interval, not the simulated five-second example.
Problem examples can contain exactly `type`, `title`, `status`, `detail`,
`code`, `message`, and `correlationId`, with `additionalProperties: false`;
if the agreement requires `detail == message`, author those examples that way.
Static dates and all other example fields are preserved.

## Manifest configuration

Both contract and manifest opt-ins are required. Missing configuration never
means allow-all. All added model fields reject unknown keys:

```yaml
client: demo
displayName: Demo
targets: {consumer: rest}
mcpExposure: {mode: perApi}
inboundAuth:
  mode: subscriptionKey
  subscriptionKeyHeader: x-api-key
apis:
  - name: things
    displayName: Things
    backend: {mode: mock}
    mcpTools: [get-thing-status]  # Selected operation metadata, not an MCP endpoint.
    runtime:
      allowedSubscriptionIds: [demo-pilot]
      rateLimit: {calls: 60, renewalPeriod: 60}
      # simulate: {status: 429} # Or 503; optional named example selector.
```

The rate is an explicit operator choice, **not** an inferred agreement default.
`calls` is an integer in 1..1000000; `renewalPeriod` is in 1..300 seconds.
Subscription IDs are the APIM subscription **resource names/identifiers**, not
keys, ARM resource paths, Azure subscription IDs, or users. Only API-scope-valid
active subscriptions are recognized by APIM. A key valid for some unrelated API
or product is **not** recognized as a forbidden caller; APIM normally rejects
it as invalid (401). A 403 means an otherwise API-scope-valid subscription
failed this explicit ID allowlist. Review the generated `<client>-pilot`
product subscription deliberately; include its ID only if it should be allowed.

`subscriptionKeyHeader` must match the selected contract security scheme.
Other clients retain `Ocp-Apim-Subscription-Key`. Custom headers are REST-only
in this release. APIM still has its query-key setting, but runtime v1 also
requires the configured header, so a query-only credential receives 401.
Keys are retrieved privately through separately approved operational processes.

Automatic mock variants validate runtime policies using the renamed API and
target manifest before any writes. Only an exact `<source-client>-pilot`
allowlist entry becomes `<target-client>-pilot`; other subscription IDs are
preserved unchanged, not inferred or renamed by prefix. Review those retained
IDs explicitly for the new client's authorization scope. If this mapping
creates a duplicate allowlist entry, preparation fails rather than silently
deduplicating it. Correlation bindings, security declarations, response examples
and operator simulation configuration remain unchanged.

## Ordering, response emission and limitations

For a matched operation, generated behavior is:

1. APIM subscription authentication; missing/invalid keys map to the contract 401.
2. Explicit subscription-ID authorization; rejected callers receive contract 403.
3. Input validation; invalid IDs or missing/invalid correlation UUIDs receive 400.
4. Per-subscription API-scope `rate-limit`.
5. Optional operator-configured forced 429/503.
6. Ordered `x-mock` lookup and its required final default.

Correlation is prepared before policy rejection without making an input decision
before authentication. Valid caller UUIDs are echoed byte-for-byte; invalid or
missing values become new UUIDs. The early-error path independently prepares
correlation because subscription validation may fail before inbound policies.
Every generated `return-response` sets its own headers and body: outbound
policies are not relied on. Raw gateway/provider error messages are never copied
into the mapped problem response.

The generated product limiter excludes only APIs with explicit runtime
throttling. Other APIs retain the existing product limit. Operator simulation is
source configuration requiring rebuild/review/deployment, never a request header,
query flag or MCP tool argument. Actual 429 handling maps only this generated
limiter's `RateLimitExceeded`, using `retry-after-variable-name`.
The generated header converts that object-valued variable to `System.Int32`
before formatting; it never calls the unsupported `System.Object.ToString()`
or substitutes a fixture interval. The policy reference defines seconds but
does not specify the variable's boxed CLR type, so conversion is used rather
than assuming an exact boxed `int`.

**Live acceptance remains necessary.** Existing service/workspace/operation
policies can preempt the API pipeline; product policies do not run for every
subscription scope. Review inherited policies and ensure no earlier throttling
or response override contradicts the agreement. APIM errors before API/operation
routing (including malformed URLs, wrong methods or an empty path segment) are
not promised to have this API's envelope or ordering. Unknown gateway failures
are not disguised as domain 503s: their normal inherited error handling remains.
Distributed rate limiting is approximate, not an exact global counter.

One policy must fit the Consumption **16384-byte UTF-8 document limit**.
The build measures and rejects oversized output without shortening examples or
losing requirements. Reduce payloads only with approval, or implement a separate
runtime; do not simply select native MCP while retaining this REST-only extension.
Encoded JSON bodies are generated data, never arbitrary contract-supplied code.
Partial-deployment recovery still requires exact non-payload structure, header
configuration and ownership; dynamic policy-body changes are deliberately not
eligible for the old literal-body-only recovery exception.

## Compatibility and verification

`when.caseSensitive: true` is independently supported by REST/native selection,
policy MCP selection, and sample/verifier matching. Omitting it (or false)
preserves the historical case-insensitive behavior; it is invalid on `missing`.
This option alone does not enable runtime validation.

Both MCP profiles reject `x-kit-runtime` and custom subscription headers during
profile validation/handoff. They do not reinterpret transport headers as tool
arguments or claim equivalent UUID/error/auth behavior. Native MCP resources are
also suppressed defensively in generated REST-only Bicep. Facades, external
backends and Entra-JWT-plus-runtime are unsupported by version 1.

Run normal offline preparation before separately approved deployment:

```text
mcp-kit prepare clients/demo --profile rest-consumption
mcp-kit consumer-handoff demo --profile rest-consumption
```

After separately approving the real target and caller, ordinary `verify-rest`
checks reachable `x-mock` branches (or the configured simulated response),
custom key header, correlation binding, static headers and unchanged example
fields. It does not exhaustively test negative auth, authorization, every
invalid input, rate exhaustion, inherited policy behavior or concurrency.
Its static named-fixture mode refuses this extension rather than falsely
comparing dynamic results to a literal correlation fixture. Perform separately
reviewed acceptance for missing/invalid keys, valid-but-disallowed subscriptions,
UUID spelling/casing/nil, invalid IDs, unknown valid IDs and real retry intervals.
No offline output, handoff or generated hash proves a deployed endpoint.

References: Microsoft documents [on-error handling across all tiers](https://learn.microsoft.com/azure/api-management/api-management-error-handling-policies),
[per-subscription rate limits and retry intervals](https://learn.microsoft.com/azure/api-management/rate-limit-policy),
and [return-response pipeline termination](https://learn.microsoft.com/azure/api-management/return-response-policy).

## Version 2: multi-operation stateless JSON REST (1.7)

Opt in by changing the root `x-kit-runtime.version` to **2**. V1 contracts keep
their original compiler/output and single-GET constraints. V2 supports 1..32
GET/POST/PUT/PATCH/DELETE operations per API, subject to the **whole API policy's
16 KiB limit**, not 16 KiB per operation. All existing mock/public/REST/perApi,
security, response-envelope, example and explicit manifest requirements apply.
Bodyless 204 responses and GET request bodies are not in this subset.
POST/PUT/PATCH/DELETE mocks are simulations: no persistence, deduplication,
transactions, generated business logic or callback delivery.

V2 request bodies accept exactly `application/json`, with optional charset.
APIM `validate-content` enforces the **imported operation schema**, not a
separately maintained schema. Required/absent bodies and media types are checked
explicitly. The body limit is 102400 bytes. Validation errors map to the declared
400; the implementation does not expose gateway diagnostics in that body.

Supported JSON schema subset:

| Type | Constraints |
|---|---|
| object | properties, required, boolean additionalProperties |
| array | items, minItems/maxItems (0..1000 when supplied) |
| string | minLength/maxLength, bounded ASCII pattern, uuid, string enum (v1 string subset) |
| integer/number | inclusive minimum/maximum and typed enum, finite safe JSON numeric bounds |
| boolean | typed enum |

Local nonrecursive references are supported, up to 8 levels/128 expanded nodes.
Each object has at most 64 declared properties. Nullable, schema compositions,
arbitrary formats/regexes and unsupported constraints fail at build time; they
are never discarded. Path/query/header inputs retain the v1 string-only subset.
Schema import/validation behavior and inherited policies need live APIM
acceptance on the target gateway; offline schema tests are not a substitute.

V2 keeps response example JSON opaque while binding correlation and preserves
literal header values, including timestamp offsets. Its offline expression
probe compiles and executes the emitted C# when an existing .NET SDK is
available; it does not emulate APIM schema validation or distributed counters.

### Composite mock conditions

```yaml
x-mock:
  - when:
      all:
        - body: /customer/id
          equals: C-1
          caseSensitive: true
        - any:
            - body: /urgent
              equals: true
            - body: /lines/0/quantity
              equals: 2
    respond: {status: 200, example: priority}
  - respond: {status: 200, example: normal}
```

`all`/`any` contain 2..8 conditions, at most 16 nodes/3 nesting levels per rule.
Leaves use either an existing `param` or a non-root JSON Pointer `body`.
Pointers follow declared properties and explicit bounded array indexes; no
JSONPath, wildcards, scripts or arbitrary expressions. String leaves support
equals/contains/startsWith and optional caseSensitive. Numbers/booleans support
typed equals. `missing: true` tests absence (not explicit null) for body fields.
Rules are ordered and require a final default. Only runtime v2 accepts this
syntax; neither legacy mocks nor MCP silently reinterpret it.

### Per-operation configuration

Root correlation/errors are defaults. Optional `x-kit-runtime.operations`
overrides use exact operation IDs and replace a complete correlation or errors
group; ordinary response declarations provide each operation's static headers.
The manifest can override operator simulation and add an operation rate limit:

```yaml
# Inside apis[].runtime, alongside allowedSubscriptionIds and rateLimit:
operations:
  create-order:
    rateLimit: {calls: 5, renewalPeriod: 60}
    simulate: {status: 503}
```

Operation limits are **additional** to the explicit aggregate API limit; they
do not create independent unlimited counters. The API policy validates input
and enforces its aggregate limit before returning through an operation policy
for an optional additional counter and final response. `rate-limit` occurs
once per policy, at API or operation scope, without product-only API children.
Every v2 operation has a generated policy; removing an override replaces its
limiter with inheritance only, so a stale operation limit does not survive.
Each operation policy is also checked against 16 KiB; this never relaxes the
whole API policy limit or moves response examples out of that budget. Simulation remains an
operator source change, not a request flag. Unknown override IDs fail before
writes; automatic client variants rename both contract and manifest references.

V2 evaluates inherited inbound policies first, then its own authentication,
allowlist, scalar/body validation, limiter and mock selection. Its on-error
mapping runs for known routed operations only. Inherited policies may preempt
the pipeline; pre-routing and unknown failures are not disguised as domain errors.
This differs from v1's inherited-base placement; v1 output remains unchanged.

Normal `verify-rest` derives schema-valid request witnesses for composite/body
branches and uses operation-specific correlation/headers/simulation. Search is
bounded to 128 witnesses; if no witness is found it stops **before calls** and
asks for representative request examples or rule review, not a weaker contract.
This does not prove mathematical unreachability or exhaustively check negatives.
The automatic coverage report includes the remaining acceptance checks.

The generated compiler deduplicates identical data and branches, never truncates
examples. If the complete policy exceeds the limit, reduce examples only with
approval, split whole operations into separate API contracts, or implement a
different runtime. MCP adaptation and stateful backends remain separate milestones.
