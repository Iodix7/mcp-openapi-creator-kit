# Consumer targets e AI Gateway tier preview

L'export sperimentale produce **solo piani offline**, non deployment.
`targets` è indipendente da `GATEWAY_PROFILE`: `native-mcp`,
`policy-mcp-consumption` e `rest-consumption` restano invariati, così come
i flussi esistenti VS Code e Copilot Studio.

## Companion locale e MCP applicativo

Il companion MCP locale espone workflow, catalogo, dashboard e controlli del
workspace in sola lettura. Non è il server MCP dei tool aziendali: questi sono
gli endpoint distribuiti su APIM a partire dai contratti OpenAPI.

I tool `target-capabilities` e `target-report` mostrano compatibilità, file
previsti, warning e blocker in memoria. Il pannello **Consumer targets** del
dashboard mostra lo stesso report. Nessun comando di export o deploy viene
esposto dal companion; token loopback, escaping e CSP restano invariati.

## Configurazione client

La configurazione operativa rimane `clients/<id>/mcp-manifest.yaml`.
Senza `targets` i default sono `consumer: copilot-studio` e
`gateway: existing-apim`. I consumer accettati sono `copilot-studio` e `rest`;
i gateway sono `existing-apim` e `ai-gateway-preview`.
Campi sconosciuti, tipi errati e metadati preview mancanti vengono rifiutati.

```yaml
targets:
  consumer: copilot-studio
  gateway: ai-gateway-preview
  preview:
    region: eastus2
    source: openapi
    restBaseUrls:
      customer-care: https://existing-backend.example.org/sample/customer-care
```

Il base URL HTTPS è esplicito per API: il kit non lo deduce dall'URL MCP o da
placeholder. Deve esistere un servizio REST funzionante: importare OpenAPI
non esegue gli example o le regole `x-mock`. Il consumer `rest` riceve un
blocker: il tier espone tool MCP, non un nuovo endpoint REST equivalente.

Per `source: remote-mcp`, usare `remoteServers` con `name`, `url` HTTPS e
`auth` (`none`, `apiKey`, `oauth2-interactive`, `managedIdentity`).
Solo `apiKey` richiede `secretRef`, il nome del segreto, mai il valore.
Non combinare `remoteServers` con `restBaseUrls`. Il piano non inventa
separatori di namespace o payload ARM.

## CLI offline

```powershell
python tools\export-target.py sample --report
# Richiede targets.gateway: ai-gateway-preview nel manifest:
python tools\export-target.py sample
# Stesso entry point dopo installazione wheel:
mcp-export-target sample --root C:\path\to\workspace --report

python experimental\ai-gateway-preview\plan.py sample --report
python experimental\ai-gateway-preview\plan.py sample --apply
# --apply termina con exit 2 prima di qualunque chiamata Azure.
```

`--report` è read-only anche per i normali target `existing-apim`.
La scrittura richiede esplicitamente `ai-gateway-preview` e genera
`ai-gateway-plan.json` e le proiezioni `gateway-openapi-<api>.json` nella sola
area `clients/<id>/generated/targets/`. Per sorgenti MCP remoti basta il piano.
I file obsoleti vengono rimossi solo da questa area; non inserirvi file manuali.
Gli output sono ignorati da Git.

### Proiezione OpenAPI

- Esporta solo le operazioni selezionate in `mcpTools`, senza modificare
  contratti, operationId kebab-case, example o header Idempotency-Key.
- Usa OpenAPI 3.0.x e lo schema ufficiale vendorizzato.
- Risolve solo riferimenti locali ai componenti, con budget di espansione e
  rilevamento cicli; non scarica risorse esterne.
- Rifiuta ref esterni, esempi esterni, path-item ref, callback, link e
  discriminator non proiettabili senza riferimenti pendenti.
- Segue il contesto strutturale, anche quando le proprietà si chiamano
  `content`, `headers`, `responses`, `schema` o `$ref`.
  Nei payload letterali degli esempi, default ed enum, `$ref` resta un dato.
- Rimuove `x-mock` operativo e gli override di routing/auth dalla proiezione.
  L'autenticazione backend va configurata e verificata separatamente.

## Limiti preview e gate live

La matrice registra `eastus2`/`swedencentral`, management API
`2026-05-01-preview` e header runtime `api-key` gateway-wide. Questo è il
**tier preview**, non il supporto policy AI di APIM classico.
Non si promettono SLA, prezzi garantiti o serving mock.

Il contratto management completo (resource paths, tipi, payload e gestione
credenziali) non è verificato: **apply rimane bloccato**, anche se l'operatore
approva un ambiente. `offlineReady` indica soltanto che la proiezione locale
non ha errori; non significa deploy consentito o validazione live riuscita.
Il piano mantiene `applyAllowed: false`, `liveVerified: false` e i blocker.

Prima di un eventuale pilot manuale verificare account, tenant, subscription,
ambiente azd, resource group, regione, gateway profile e gateway target.
Azure CLI e azd hanno contesti indipendenti. Usare un gateway isolato perché
la chiave runtime concede accesso a tutti i modelli e tool del gateway.
OAuth interattivo non equivale a client credentials.
Vedere [pilot locale e gate live](pilots/README.md).

## Fonti e verifiche

Lo schema OpenAPI è incluso nel wheel in
`src/mcp_openapi_creator_kit/schemas/openapi.json`; URL originale, data e
SHA-256 sono in `sources.json`. Nessun aggiornamento o download a runtime.

- [AI Gateway overview](https://learn.microsoft.com/azure/api-management/ai-gateway-overview)
- [Creazione preview](https://learn.microsoft.com/azure/api-management/quickstart-ai-gateway-create)
- [Tool e federazione](https://learn.microsoft.com/azure/api-management/ai-gateway-manage-models-tools)

Le regressioni in `tools/tests/test_consumer_targets.py` coprono selezione,
determinismo, schema, riferimenti strutturali e sicurezza dei percorsi.
`test_target_pilots.py` verifica il consumo locale HTTP/MCP e le superfici
read-only, non il servizio Azure.

Resta la correzione alle sette risposte 401/404 mal indentate nel sample:
è una correzione OpenAPI indipendente, senza cambi a operationId o payload.
