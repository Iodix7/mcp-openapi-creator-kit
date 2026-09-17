# Gateway retirement safety plan: execution blocked

**There is no supported `mcp-kit retire-gateway` command in this release.**
Standalone provisioning is implemented; automatic disposable-gateway cleanup
is not. The existing `mcp-kit retire` remains client-only. Do not widen that
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

Accordingly, the supported release boundary is **creation and client
deployment/retirement only**. New-gateway acceptance must remain pending scoped
resource approval and a separately validated gateway-cleanup design; it cannot
claim automatic retirement or zero remaining cost. Route an already-created
service to the operator's separately approved resource-management process,
without generating or executing a by-name deletion workaround.

References:
[Service GET, including ETag](https://learn.microsoft.com/rest/api/apimanagement/api-management-service/get?view=rest-apimanagement-2024-05-01),
[Service DELETE contract](https://learn.microsoft.com/rest/api/apimanagement/api-management-service/delete?view=rest-apimanagement-2024-05-01).
