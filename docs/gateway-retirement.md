# Gateway retirement safety plan: execution blocked

**There is no supported `mcp-kit retire-gateway` command in this release.**
Standalone provisioning is implemented; **general-purpose gateway cleanup is
not**. The narrowly scoped exclusive E2E exception below is not gateway
retirement. The existing `mcp-kit retire` remains client-only. Do not widen that
command, issue APIM DELETE manually by name, remove the resource group, or
report cleanup as completed.

The installed provisioner now writes a durable create receipt and assigns
two kit ARM tags to the new service. Those are necessary audit foundations,
not sufficient deletion authorization. No existing service can be adopted:
missing, partial, edited/imported or mismatching receipts and missing/changed
tags must block an eventual gateway-retirement command.

## Required separately reviewed deletion scope

After separately approved client retirement, a future dedicated command may
propose **only the exact APIM service resource ID** and its already-proven
empty, supported service-managed graph. It must preserve the existing RG,
deployment-history records, customer data and every unrelated resource.
No name search, wildcard, resource-group deletion, purge or implicit cleanup
of foreign resources is acceptable.

The review must display account, tenant, subscription, RG, full APIM ID,
location, tier, profile, native identity deletion effect, creation timestamp,
deployment ID, observed ETag, source/receipt/inventory fingerprints and the
complete set of direct/cascading IDs. Preview must never delete. Apply needs a
freshly computed matching gateway-specific review token **and an exact typed
confirmation of the full service ID**, not just `--yes`, a profile or a name.
Tokens and receipts are not proof of human approval.

## Gates that must all be demonstrated before shipping execution

1. **Creation provenance:** validate the immutable local receipt against the
   original successful deployment, compiled creation template/parameters and
   installed kit metadata. The current service must match the exact recorded
   resource ID, both kit tags, `createdAtUtc`, tier and managed identity.
   A same-name replacement is not the same resource incarnation. Missing or
   unverifiable deployment history/receipt blocks; never regenerate ownership
   evidence from a current service GET.
2. **Complete supported graph:** enumerate every relevant APIM control-plane
   collection and service-associated extension resource with strict pagination,
   scope validation and explicit error propagation. Empty APIs/products are
   not enough: backends, named values, certificates, loggers/diagnostics,
   policies/fragments, gateways, portal configuration/content, identities,
   users/groups, subscriptions and associations can remain. Known default
   objects need an explicit, versioned, tested service-default allowlist;
   unknown or foreign resources and inaccessible collections block. Generic
   ARM resource-list output alone does not prove all APIM child collections
   are empty.
3. **External relationships:** prove the absence of foreign role grants,
   network/private-endpoint relationships, locks, diagnostics and other
   dependencies that service deletion could orphan or affect. If required
   permissions or a complete bounded discovery mechanism are unavailable,
   block instead of claiming no dependencies.
4. **Freshness:** bind the receipt, installed code, target, ETag and complete
   graph to the review fingerprint. Re-read them immediately before DELETE.
   A parent APIM ETag is not documented as changing for every child/association
   mutation; therefore parent ETag equality cannot replace graph reinspection.
5. **Conditional deletion semantics:** establish that the chosen service API
   actually enforces the intended ETag precondition. Do not merely append an
   `If-Match` header and assume the provider honors it. Concurrent operations
   after the last check still need an explicitly documented safety boundary.
6. **Outcome verification:** handle accepted asynchronous deletion with bounded,
   identity-scoped polling. Only documented absence proves success. Forbidden,
   malformed, timed-out or incomplete reads are not absence. Preserve audit
   receipts/history; do not retry, purge or attempt a rollback automatically.

## Why execution remains blocked

The official APIM **service GET** reference includes an `etag` in its response.
The **service DELETE** reference for API version `2024-05-01` documents the
service/subscription/RG/API-version parameters and asynchronous responses, but
does not document an `If-Match` precondition. That is not proof that conditional
deletion works, nor proof that it cannot work: it is unresolved evidence that
must not be replaced with an assumption.

The existing client-retirement implementation proves ownership only for its
supported client graph, not all resources and external dependencies of an APIM
service. No live disposable-service experiment is approved here. A mocked test
that always honors ETags or returns empty collections would not establish the
missing Azure behavior.

Accordingly, the general supported release boundary is **creation and client
deployment/retirement only**. General new-gateway acceptance must remain pending scoped
resource approval and a separately validated gateway-cleanup design; it cannot
claim automatic retirement or zero remaining cost. Route an already-created
service to the operator's separately approved resource-management process,
without generating or executing a by-name deletion workaround.

## Explicit operational exception: exclusive temporary E2E service

The optional retained consumer stage uses this same boundary, not a broader
deletion authority. An approved Studio test may consume the isolated mock
endpoints, but no other writer or control-plane reuse is permitted while paused.
No cleanup timer is scheduled: the service remains live until explicit cleanup,
which may finish an abandoned consumer test without marking it passed.
Studio agents/connections are outside this service cleanup scope.

The installed-package E2E runner may use
`mcp_openapi_creator_kit.ephemeral_gateway.cleanup` for a **new, dedicated test
service only**, after separate prior authorization to delete the **entire exact
service resource ID** and explicit acceptance of exclusive use throughout the
test. This is an operational assumption: nobody modifies, shares, or reuses the
service while the run or cleanup is in progress. It is **not atomic protection
against concurrent writes**. Implementation approval alone does not approve a
live run. Existing/shared services are excluded.

The exception deliberately does **not** claim the generic gates above have
been solved or relax them for general retirement:

- The service name must be exactly `mcp-kit-e2e-<32 lowercase hex digits>` derived
  from the **full run UUID**, not a shortened collision-prone run identifier.
  Only `policy-mcp-consumption`, Consumption capacity 0, public networking and
  identity `None` are eligible.
- The existing resource group, deployment history and unrelated resources
  remain intact. No RG deletion, purge, permission change, lock removal or
  automatic adoption is supported.
- The runner supplies its original successful provisioner report and original
  in-workspace creation receipt. Local scope, paths, context, installed input
  fingerprints, compiled template and parameter artifacts are validated before
  any Azure call. Symlink, junction, hard-link, missing and mismatching evidence
  blocks cleanup. Receipts are audit records, not signed attestations or deletion
  authorization; an imported/user-written receipt or a current GET alone cannot
  establish provenance.
- Live verification requires the exact successful original deployment, its
  parameters, single service output and exported creation template, plus the
  original service ID, creation timestamp, exact tags, tier and network settings.
  Historical templates are never executed. Uncertain creation or failure to
  issue the original receipt blocks cleanup; it is never silently adopted.
- Authorization covers the whole exclusively used test service, **including
  service defaults such as the Consumption all-access subscription** and the
  run's deployed test children. There is no generic exhaustive-child adoption
  allowlist. This trade-off is valid only under the explicit exclusive-use
  assumption, not for a shared APIM.
- Client deployment may change the APIM ETag. Cleanup does not demand the old
  creation ETag or send an undocumented `If-Match` precondition. Neither the
  stable `2024-05-01` nor preview `2025-09-01-preview` service DELETE reference
  documents such a precondition or an atomic child-graph deletion guard.
- A durable local attempt marker is created **before** the single exact-service
  DELETE. A crash, timeout or unknown submission result never triggers another
  DELETE. An explicitly invoked rerun with that marker is observation-only.
  Preserve the marker and receipt; deleting them is not a retry mechanism.
- DELETE acceptance is not completion. Bounded polling requires a successful,
  complete, scope-validated resource-group APIM service collection in which the
  exact ID is absent. Failed, forbidden, malformed, paginated-incomplete or
  timed-out reads are not absence; neither is an arbitrary HTTP 404.
  A changed incarnation or changed tags while waiting blocks the result.

The primitive returns `status: deleted` only for verified active-service
absence, with `serviceAbsent: true` and a separate `softDelete` retention notice.
Otherwise it raises `CleanupError` (a `RuntimeError`) with an actionable,
bounded error message. Its safe `report` attribute contains `status: blocked`,
the reason and attempt/observation state; it never claims successful cleanup
from an accepted request. The runner requires fresh explicit exclusive-use,
full resource-ID and subscription approval for each run or explicit cleanup.
Deadlines bound the operation and each CLI invocation;
transport process-tree termination may need its separate bounded cleanup time
after a command timeout. Local synthetic tests validate this control flow,
**not Azure provider semantics**.

### Soft-delete is not permanent destruction

APIM soft-delete applies to **all service tiers, including Consumption**.
The service remains recoverable for **48 hours**, and its name is reserved
during retention. Purge is a separate operation and is deliberately unsupported
here. Active-service absence does not prove that soft-deleted retention has
ended; reports must not promise permanent destruction, immediate name reuse,
zero remaining cost or zero billing. Use a new full UUID for a new run rather
than trying to purge or reuse a retained name.

References:
[Service GET, including ETag](https://learn.microsoft.com/rest/api/apimanagement/api-management-service/get?view=rest-apimanagement-2024-05-01),
[Stable service DELETE contract](https://learn.microsoft.com/rest/api/apimanagement/api-management-service/delete?view=rest-apimanagement-2024-05-01),
[Preview service DELETE contract](https://learn.microsoft.com/rest/api/apimanagement/api-management-service/delete?view=rest-apimanagement-2025-09-01-preview),
[APIM soft-delete, retention and purge](https://learn.microsoft.com/azure/api-management/soft-delete),
[APIM subscriptions and built-in all-access subscription](https://learn.microsoft.com/azure/api-management/api-management-subscriptions).
