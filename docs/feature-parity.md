# Mappa funzionale: repository originale e plugin installabile

Data della ricognizione: 2026-09-11.

Le tabelle e l'inventario iniziali conservano la fotografia dell'11 settembre.
Per le modifiche successive e l'esito cloud fa fede
[l'aggiornamento del 16 settembre](#aggiornamento-del-2026-09-16-azure-live-e-retirement):
il retirement mock installato e il test Azure sono ora completati.
Le ulteriori modifiche locali sono elencate nella
[chiusura funzionale del candidato 1.2.0](#candidato-120-chiusura-funzionale).
Le vecchie righe non sono lo stato corrente del candidato.

Questa mappa confronta le funzionalita' del kit originale con il prodotto
installabile corrente. **Non dichiara completato il porting**: distingue codice
disponibile, procedure manuali, differenze di comportamento e verifiche realmente
eseguite.

## Perimetro ed evidenze

- **B**: checkout originale di riferimento, HEAD
  `70d0b148bfb9fc49b437d05d60c9d3860466b617`.
- **N**: checkout corrente `mcp-openapi-creator-kit`, HEAD
  `e5c1b42163da82834e67b82090847e95c36e3514`, incluse le modifiche locali.
  Non e' un confronto fra due release pulite o pubblicate.
- **S**: `N/src/mcp_openapi_creator_kit`.
- L'E2E del wheel 1.1.1 ha verificato il flusso **offline tramite Copilot CLI
  nativo**, con ripresa assistita dopo correzioni del runner. Non prova VS Code,
  deployment Azure o affidabilita' generale del modello.
- La ricognizione Azure successiva ha letto account/subscription e inventario
  APIM; non ha applicato modifiche. Non va confusa con un test di deployment.

Ci sono due MCP distinti: il **companion locale** del plugin aiuta a creare e
gestire lo scenario; gli **endpoint business su APIM** espongono i tool derivati
dai contratti. Le resource e i prompt del companion non aggiungono resource e
prompt agli endpoint business.

### Legenda

| Stato | Significato |
|---|---|
| Disponibile | Funzionalita' presente nel percorso installato |
| Estesa/nuova | Funzionalita' aggiunta o ampliata rispetto al kit originale |
| Parziale | Solo una parte del percorso o del contenuto e' trasferita |
| Modificata | Comportamento diverso: non assumere compatibilita' identica |
| Solo repo | Codice/procedura conservato nella repo, non nel flusso standalone |
| Non supportata | Non implementata operativamente; non necessariamente una regressione |

## 1. Discovery, specifica e contratti

| # | Funzionalita' | Stato nel plugin/kit installato | Evidenza principale |
|---|---|---|---|
| 1 | Discovery: persona, job-to-be-done, outcome, momenti di lavoro e chiarimenti | **Parziale**. Nucleo trasferito; domanda sul livello di realismo per API e chiusura pre-mortem non sono piu' esplicitamente richieste come nella procedura originale. Il template puo' ancora contenere sezioni pertinenti. | B/N `skills/discovery.md`; S `guidance.py::workflow_guide`; `plugin.py::GUIDANCE_ASSETS` |
| 2 | Storyline annotata, mapping tool/example, criteri mini-EARS, conferme e rifiuti motivati | **Disponibile**. Rimangono contenuti da elaborare e revisionare, non una garanzia automatica di comprensione del modello. | N `skills/discovery.md`, sezioni Cover these areas e Viability gate |
| 3 | Specifica coerente con contratti e manifest | **Estesa/nuova**. `scenario-contract` legge l'inventario; `spec-sync` genera il blocco tecnico, preserva narrativa e newline, rileva obsolescenza e draft irrisolti. Non certifica la semantica business o l'approvazione umana. | S `scenario.py::inventory`, `plan_spec_sync`, `apply_spec_sync`, `check_text` |
| 4 | Costituzione, procedure e handover | **Estesa/nuova**, con contenuti rielaborati. Documenti packaged condivisi fra tool/resource/prompt; il nuovo HANDOVER non riproduce integralmente tutti i questionari originali. | S `_build.py::ASSETS`, `guidance.py::document`, `REFERENCES`; B/N `HANDOVER.md` |
| 5 | Consultare operazioni e schemi prima di inventare nuove API | **Disponibile**. Inventario, usi, proprieta', catalogo schemi e ricerca del companion. | B/N `tools/build-facade.py::print_catalog`, `print_schema_catalog`; S `server.py::catalog_search` |
| 6 | Validazione manifest, operationId, riferimenti, example, errori e idempotenza | **Disponibile**, con gate esplicito OpenAPI 3.0.x. Controlli strutturali secondo gli standard configurati; descrizioni, paginazione e scritture asincrone non equivalgono a validazione semantica completa. | B/N `tools/build-facade.py::validate_manifest`, `validate_standards`, `validate_examples`; N `validate_openapi_version` |
| 7 | Mock contract-first e regole `x-mock` | **Disponibile**. Query/path/header, condizioni `equals`, `contains`, `startsWith`, `missing`, status/example e fallback deterministici. Nessuno stato o calcolo business aggiunto. | B/N `build-facade.py::xmock_condition`, `xmock_response`, `compile_xmock_blocks`; S `policy.py::mock_rules` |
| 8 | Libreria condivisa e schemi canonici | **Disponibile**. Conflitti strutturali canonici bloccanti; omonimi divergenti e duplicati segnalati; schemi canonici packaged e locali. | B/N `tools/build-facade.py`, validazioni cross-contract |
| 9 | Collisioni di nomi, path, componenti e tool fra clienti | **Disponibile**. Controlli cliente/facade e unicita' dei `mcpTools` cross-client; avviso sui contratti condivisi. La regola di non modificare un contratto condiviso non impedisce materialmente le scritture dell'host. | B/N `tools/build-facade.py::build_client`, `main` |
| 10 | Varianti cliente senza alterare l'originale | **Estesa/nuova**. Preview, `--write`, nuove cartelle, prefissi operationId, link e controllo collisioni. Automazione mock-only senza credenziali outbound; non e' un rename automatico di un cliente esistente. | N `tools/prepare-variant.py::prepare`, `_validate_refs`, `_rewrite_links`, `apply` |
| 11 | Intera libreria di esempi e scenari verticali originali | **Parziale / Solo repo**. Il wheel include lo starter `customer-care`/`sample`, non automaticamente tutta la libreria originale o gli esempi verticali. Altri dati richiedono migrazione esplicita. | S `_build.py::ASSETS`; N `docs/customer-workspace-migration.md`; B `README.md` |

## 2. Esposizione API e profili

| # | Funzionalita' | Stato nel plugin/kit installato | Evidenza principale |
|---|---|---|---|
| 12 | Esposizione `facade`, `perApi`, `both`, routing e mapping request/response | **Disponibile**. Composizione conservata; `mapping.xml` resta supportato per external. `both` puo' duplicare tool nel consumer. | B/N `build-facade.py::build_client`, `mapping_sections`, `url_template_condition`, `build_facade_policy` |
| 13 | `native-mcp`, `rest-consumption`, `policy-mcp-consumption` | **Disponibile** per contratti/generazione. Consumption resta public/mock-only. Un report offline non prova la compatibilita' di un gateway reale. | S `catalog.py::PROFILES`, `policy.py::load_client`; N `tools/validate-deployment-profile.py` |
| 14 | Sharding policy a 16 KiB senza spezzare/ridurre un singolo tool | **Disponibile ma modificata** la convenzione URL: nel multishard originale il primo era `...-policy-mcp-1`; ora resta `...-policy-mcp`, seguito da `...-policy-mcp-2`. Considerarlo nella migrazione dei consumer. | B `tools/mcp_policy.py::shard_tools`, `build_client_plan`; S `policy.py::shard_tools`, `build_client_plan` |
| 15 | Schema tool policy-MCP derivato da OpenAPI | **Modificata**. `Idempotency-Key`, prima esposta, viene nascosta al modello e generata internamente nel nuovo policy MCP. Il requisito del contratto REST resta. Non dichiarare identita' dei descrittori o degli input MCP. | B `tools/mcp_policy.py::tool_input_schema`; S `policy.py::is_model_visible_parameter`, `tool_input_schema`, `tool_call_branch` |
| 16 | Passaggio mock -> external per API | **Disponibile** nel profilo native MCP tramite manifest, URL/outbound auth e secretRef. Consumption non ammette external; i sistemi reali e i segreti non vengono creati dal companion. | N `skills/lifecycle.md`, Mock to external; `tools/deploy-client.py`; `tools/deployment.py::check_secrets` |
| 17 | Auth, policy e segreti durante il riuso di APIM | **Disponibile con limiti**. Policy cliente e controlli metadata/permessi sui secretRef; nessun vault fittizio richiesto nei mock. Risorse, segreti e autorizzazioni esterne necessari devono gia' esistere. | N `modules/client-product.bicep`; `tools/deployment.py::check_secrets`; `docs/selective-deployment.md` |

## 3. Azure, provisioning e lifecycle

| # | Funzionalita' | Stato nel plugin/kit installato | Evidenza principale |
|---|---|---|---|
| 18 | Creare APIM quando non esiste | **Solo repo: gap di porting**. Il Bicep originale e' conservato, ma non esiste un comando standalone di provisioning equivalente. | B/N `infra/main.bicep`, `platform/platform.bicep`; S `runtime.py::COMMANDS`; N `skills/onboarding.md`, Legacy full-platform provisioning |
| 19 | Provisioning completo di piattaforma e tutti i clienti | **Solo repo**. `azd up` e indici multi-client non diventano un flusso del plugin data-only. | N `infra/main.bicep`; `skills/onboarding.md`; S `cli.py::main` |
| 20 | Creazione/configurazione rete, Key Vault, identita', RBAC e telemetry di piattaforma | **Solo repo** per il provisioning. Il deploy selettivo non crea o modifica queste risorse di piattaforma; puo' verificarne prerequisiti e bloccare configurazioni incompatibili. | N `platform/platform.bicep`; `docs/selective-deployment.md`; `tools/deployment.py::inspect_resources` |
| 21 | Scelta del profilo tramite capacita' reali del gateway | **Estesa/nuova**. Inspector e stato condiviso, evidenze temporanee/session-local; i fatti dichiarati dal modello non sostituiscono l'ispezione. L'assenza di accesso cloud consente preparazione provvisoria. | S `gateway.py`, `workflow.py`; N `skills/onboarding.md` |
| 22 | Preparazione locale e stato del percorso | **Estesa/nuova**. `prepare`, ricevute locali, stato corrente/obsoleto, `currentStep` e `nextInvocation` con runtime e argomenti esatti. Nessuno di questi esegue automaticamente il comando suggerito. | S `cli.py::main`, `progress.py::begin_step`, `finish_step`, `with_progress`, `workflow.py` |
| 23 | Deployment selettivo su APIM esistente | **Estesa/nuova**. Il vecchio wrapper dipendeva dagli output azd; il nuovo accetta contesto esplicito senza clone/azd e distribuisce solo il cliente selezionato. La funzionalita' esiste, ma il test cloud di questa ricognizione non e' stato eseguito. | B/N `tools/deploy-client.py::main`; N `docs/selective-deployment.md` |
| 24 | Preview ARM what-if, revisione e apply | **Estesa/nuova**. Contesto verificato, inventario, fingerprint e review token; il nuovo default e' preview. L'originale applicava direttamente dopo i controlli disponibili. | N `tools/deploy-client.py::main`; `tools/deployment.py::confirm_context`, `plan_token`, `summarize_what_if` |
| 25 | Riconciliazione di API/tool orfani durante aggiornamenti | **Disponibile** nel deploy selettivo. Ownership per prefisso piu' tag; tool nativi e server rimossi prima delle API sorgenti. Non equivale al ritiro completo del cliente. | N `tools/lifecycle.py::discover_owned_apis`, `build_plan`, `apply_plan`; `deploy-client.py` |
| 26 | Ritiro di un cliente, tombstone, riconciliazione multi-client | **Solo repo / Parziale**. `reconcile-client.py --removed-client` e `reconcile-all.py` esistono, ma non sono comandi del kit installato. La guida standalone rimanda al processo repository. | N `tools/reconcile-client.py`, `reconcile-all.py`; S `runtime.py::COMMANDS`; N `skills/lifecycle.md`, Remove or rename a client |
| 27 | Cleanup completo di tutte le risorse ausiliarie del cliente | **Non completo gia' nell'originale**. I piani attuali/originali contengono soltanto API e tool orfani. Product, pilot subscription, tag e altri artefatti non sono un piano completo di retirement. Non promettere un test usa-e-getta interamente ripulito con il solo reconciler. | B/N `tools/lifecycle.py::ReconcilePlan`, `apply_plan`, `format_plan`; N `modules/client-product.bicep` |
| 28 | Verifica REST dei mock distribuiti | **Disponibile**. Verifier installato con origine HTTPS esplicita, chiave in ambiente, casi ricavati dagli example e dai rami mock. Non chiama automaticamente backend external. | N `tools/verify-rest.py`; `tools/verification.py::gateway_origin`, `explicit_key`, `validate_manifest` |
| 29 | Verifica MCP degli endpoint distribuiti | **Disponibile con perimetro distinto**. Initialize/list e confronto tool esatto; il profilo policy MCP esercita anche `tools/call` e rami mock. Il verifier native MCP esegue discovery, non chiamate business automatiche. | N `tools/verify-mcp.py::main`, `verify_policy_tool_calls`; `tools/verification.py` |

I verifier installati accettano inbound `subscriptionKey`; `entraJwt` e `dual`
richiedono un client di verifica separato. Provare uno dei profili non dimostra
automaticamente il funzionamento degli altri.

### Perche' il cleanup va risolto prima del test live

`modules/client-product.bicep` crea un Product, una pilot subscription, una
product policy e associazioni. Il servizio contiene anche il tag cliente e le
API generate. In entrambe le repo, `ReconcilePlan` possiede soltanto
`orphan_tools` e `orphan_apis`: cancellare queste API **non costituisce una prova
di ritiro di tutte le altre risorse**.

Occorre quindi una procedura/piano di retirement completo, con ownership e
verifica finale, prima di promettere che un test di deployment lascera' il
gateway esattamente senza risorse del test. Non si tratta soltanto di aggiungere
un alias CLI al reconciler esistente.

## 4. Catalogo e dashboard

| # | Funzionalita' | Stato nel plugin/kit installato | Evidenza principale |
|---|---|---|---|
| 30 | Catalogo deterministico JSON e HTML self-contained | **Disponibile**. Motore packaged, ordinamento e template del kit; wrapper source conservato. | B `tools/build-catalog.py`; S `catalog.py::build_index`, `render_index`, `write_outputs` |
| 31 | Operazioni, schemi, risposte/example, snippet, confronto profili e composizioni | **Disponibile**. Nuove viste workflow/targets. Filtri reali: profilo/metodo/mock e ricerca; la lista composizioni non e' un filtro interattivo. | B/N `catalog/template.html::filtered`, `renderDetail`, `renderOperations`, `renderSchemas`, `renderClients` |
| 32 | Persona, job-to-be-done e outcome nel catalogo | **Parziale, gap preesistente**. Campi editoriali via metadata; nessuna proiezione automatica dalla specifica, metadata distribuiti vuoti. Questi campi non sono indicizzati dalla ricerca. | B/N `catalog/metadata.yaml`; S `catalog.py::build_index`; B/N `catalog/template.html::filtered` |
| 33 | Interfaccia IT/EN | **Parziale, regressione concreta**. Selettore presente, ma le stringhe di `T.it` nel template corrente sono inglesi come `T.en`. Non confondere la presenza del bottone con la traduzione. | B `catalog/template.html:100-103`; N `catalog/template.html:113-116` |
| 34 | Personalizzazione metadata e template | **Parziale / ownership modificata**. Metadata cliente supportati; HTML/JS provengono dal kit installato. Un template nel workspace cliente non sostituisce quello del kit. | B `tools/build-catalog.py::write_outputs`; S `catalog.py::build_index`, `render_index`; `assets.py::asset_text` |
| 35 | Dashboard locale del companion con stato workflow | **Estesa/nuova**. URL loopback, refresh e snapshot read-only condiviso. L'HTTP server vive con il processo MCP; l'HTML esportato resta consultabile separatamente. | S `server.py::dashboard_get_url`, `dashboard_refresh`; `progress.py`; `docs/e2e-testing.md` |

## 5. Companion, packaging e integrazione Copilot

| # | Funzionalita' | Stato nel plugin/kit installato | Evidenza principale |
|---|---|---|---|
| 36 | Istruzioni utilizzabili senza avere il clone cliente | **Estesa/nuova**. Wheel con codice, asset, provenienza e verifica hash; workspace dati separato dall'installazione. | S `_build.py::BuildPy.run`; `assets.py::kit_root`, `source_info`, `verify_assets` |
| 37 | Migrazione di workspace/pilot esistenti | **Parziale / manuale**. Riconoscimento directory vuota/legacy, sample distinto dai clienti attivi; nessuna migrazione automatica del pilot. | S `workspace.py::status`, `catalog`; N `docs/customer-workspace-migration.md` |
| 38 | Companion MCP locale: tool, resource e prompt | **Estesa/nuova**. Inventario esatto sotto. Legge, valida e guida; non e' il server dei tool business e non esegue scritture arbitrarie. | S `server.py::create_server`; `guidance.py` |
| 39 | Skill nativa e agente Copilot dedicato | **Estesa/nuova**. `create-mcp`, riferimenti packaged e agente dedicato. Il risultato reale verificato e' Copilot CLI; l'agente eredita gli strumenti dell'host. | S `plugin.py::SKILL`, `AGENT`, `export_plugin`; N `docs/copilot-plugin.md` |
| 40 | Export/configurazione/aggiornamento plugin | **Disponibile con limiti**. Copilot format, runtime/workspace assoluti, export esplicito. Nessuna installazione Python, modifica settings o auto-update. Spostamenti e upgrade richiedono nuovo export/reload. | S `plugin.py::export_plugin`; S `cli.py::vscode_config`; N `docs/copilot-plugin.md` |
| 41 | Installazione in un clic da GitHub/marketplace e VS Code verificato | **Non completata**. Bundle locale per installazione, non runtime portatile o marketplace pubblicato. Configurazione VS Code documentata, ma esecuzione E2E in VS Code non verificata. | N `docs/copilot-plugin.md`, Prototype boundaries; `docs/e2e-testing.md` |
| 42 | Consumer targets / AI Gateway preview | **Estesa/nuova ma offline**. Report, dashboard ed exporter separato; apply bloccato. Non equivale a deployment AI Gateway o plugin M365. | S `server.py::target_capabilities`, `target_report`; `workspace.py::target_report`; `export_cli.py::main`, `write_artifacts` |
| 43 | CI, determinismo, packaging e smoke Azure manuale | **Integrazione repo, non funzione del plugin**. Push/PR offline; workflow Azure manuale con identita'/target del fork. Non e' stato aggiunto un deploy automatico del plugin. | N `.github/workflows/ci.yml`, `.github/workflows/azure-smoke.yml`; `tools/wheel-smoke.py` |

## 6. Funzioni non supportate: non chiamarle regressioni del porting

| # | Funzionalita' | Stato reale | Evidenza |
|---|---|---|---|
| 44 | `backend.mode: hosted`, outbound `mtls`, `mcpProfile: full` | **Non supportate** gia' nell'originale; restano roadmap o valori rifiutati. | B/N `docs/roadmap.md`; B/N `build-facade.py::validate_manifest`, `validate_backend` |
| 45 | Resource/prompt degli MCP business APIM; UI MCP Apps embedded | **Non implementate come funzionalita' del prodotto distribuito**. Le resource/prompt del companion e il dashboard browser non colmano questi punti. | S `server.py` e `policy.py`; B/N `docs/roadmap.md` |
| 46 | Mock con stato, calcolo business, backend dati gestito | **Non supportati nel mock operativo**. Visione data-derived o esperimenti non equivalgono a un backend supportato. | B/N `docs/roadmap.md`; S `policy.py::tool_call_branch` |
| 47 | Solution/agent Copilot Studio generato automaticamente e portale catalogo centrale | **Non supportati operativamente**. Il collegamento al consumer resta una procedura manuale. | B/N `docs/roadmap.md`; N `HANDOVER.md`, Connect the consumer |

## Inventario esatto delle superfici installate

### Companion MCP: 14 tool

`kit-info`, `workflow-guide`, `kit-reference`, `workspace-status`,
`inspect-gateway`, `workflow-status`, `target-capabilities`, `target-report`,
`scenario-contract`, `catalog-search`, `recommend-profile`, `policy-budget`,
`dashboard-get-url`, `dashboard-refresh`.

### Companion MCP: 7 resource

`kit://constitution`, `kit://skills/discovery`, `kit://skills/onboarding`,
`kit://skills/lifecycle`, `kit://catalog/index`, `kit://workspace/status`,
`kit://workflow/status`.

### Companion MCP: 3 prompt

`discovery`, `onboarding`, `lifecycle`.

Questi conteggi derivano dalle definizioni di `S/server.py`, non dalla tabella
README. I sei tool business dello starter sono un altro inventario.

### Dashboard: browser, non MCP Apps embedded

Le sette resource sono Markdown o JSON, non HTML/app. `dashboard-get-url` e
`dashboard-refresh` pubblicano il dashboard tramite `DashboardHost` e
restituiscono `url`, `generation`, `scenarios`: non espongono metadata UI che
colleghino il tool a una resource app embedded. L'integrazione MCP Apps non e'
implementata, non semplicemente non testata. Fonte: `S/server.py::DashboardResult`,
`publish_dashboard` e definizioni resource; `S/workspace.py::dashboard`.

### CLI installata: 22 comandi

`build`, `build-policy`, `deploy`, `variant`, `validate`, `verify-mcp`,
`verify-rest`, `init`, `import-sample`, `catalog`, `target-report`, `export`,
`vscode-config`, `info`, `guide`, `reference`, `workflow-status`,
`inspect-gateway`, `prepare`, `scenario-contract`, `spec-sync`, `plugin-export`.

Fonte: `S/runtime.py::COMMANDS` e `S/cli.py::main`.
Non ci sono comandi installati `provision`, `reconcile-client`,
`reconcile-all` o `retire-client`. Non ricavarli dal nome di un file nella repo.

## Gap prioritari e verifiche residue

1. **Porting mancante:** creazione standalone del gateway e della piattaforma,
   senza tornare alla repo originale.
2. **Lifecycle incompleto:** retirement standalone e cleanup completo di tutte
   le risorse del cliente; il reconciler API/tool da solo non basta.
3. **Regressione verificata nel codice:** traduzioni dell'interfaccia italiana.
4. **Migrazione da esplicitare:** URL del primo shard e schema Idempotency-Key
   diversi nel policy MCP.
5. **Contenuti/procedura parziali:** libreria originale non tutta bundled,
   alcune domande discovery/handover non equivalenti.
6. **Gap preesistente:** metadata persona/JTBD/outcome non derivati dalla spec.
7. **Distribuzione e prova reali da completare:** VS Code, installazione
   marketplace/GitHub, deployment Azure e consumer business.

L'E2E offline dimostra un percorso funzionante per lo starter, non cancella
questi gap e non costituisce una prova del percorso completo da zero su Azure.

## Aggiornamento del 2026-09-16: Azure live e retirement

**Deploy, verifica degli endpoint e ritiro del cliente mock completati su un
APIM Consumption esistente, esplicitamente approvato dall'operatore.** Questa
evidenza aggiorna i punti seguenti senza cancellare i gap della ricognizione.

| Punti | Stato aggiornato | Evidenza |
|---|---|---|
| 23-24 | **Verificati live** per `policy-mcp-consumption`: 19 cambiamenti create-only revisionati, quindi apply selettivo riuscito dal wheel installato. | Log di preview/apply; `docs/e2e-testing.md`, Observed live mock acceptance |
| 26 | **Retirement mock standalone disponibile** tramite `mcp-kit retire`, senza clone o azd. Tombstone, riconciliazione multi-client e provisioning non diventano automaticamente comandi standalone. | `tools/retire-client.py`; `src/mcp_openapi_creator_kit/runtime.py`; `docs/selective-deployment.md` |
| 27 | **Cleanup runtime completato nel caso provato**: 10 DELETE dirette e 25 risorse figlie nel piano a cascata; assenza verificata e inventario preesistente invariato. Comprende API, Product, pilot subscription, associazioni e tag non condivisi. Non elimina lo storico deployment ARM o i dati locali. | Piano retirement revisionato, log apply, confronto inventario prima/dopo |
| 28-29 | **Verificati live**: 7 casi REST facade, initialize/list e 6 example dei tool MCP, piu' 14 controlli aggiuntivi. Auth inbound `subscriptionKey`; nessun backend reale. | Verifier installati e risultati del driver di accettazione |
| 35, 38 | **Riconfermati sul companion installato**: 14 tool, 7 resource, 3 prompt; dashboard HTTP 200 con uno scenario. Server locale terminato dopo il probe, snapshot HTML conservato. | Probe stdio MCP e HTTP del dashboard |

La CLI corrente ha **23 comandi**: i 22 dell'inventario storico piu' `retire`.
Non cambia il numero di tool/resource/prompt MCP: il retirement e' un comando
CLI trusted, non un nuovo tool che modifica Azure dal companion.

Il test ha trovato e portato a correggere problemi reali: decoding Windows
dell'output Azure CLI, lentezza del preflight retirement con molti processi CLI,
trasporto sicuro degli argomenti batch Windows e chiamata product `/groups`
non disponibile su Consumption. La causa esatta di un errore transitorio nella
lettura subscription non e' stata dimostrata; non attribuirlo automaticamente
al quoting. Tentativi falliti, correzioni e hash dei due wheel effettivamente
usati sono documentati in `docs/e2e-testing.md`.

Il retirement resta **mock-only e ownership-based**. Risorse condivise,
relazioni non possedute e stati non supportati bloccano l'operazione. Non e'
una transazione: un errore richiede una nuova preview dello stato parziale,
non DELETE manuali. Il solo SKU Consumption osservato rende `/groups`
esplicitamente `UNAVAILABLE-BY-TIER`; non trasforma errori generici in inventari
vuoti. Il contesto CLI originale e' stato ripristinato e ricontrollato.

**Restano aperti**: nuovo APIM/piattaforma dal percorso standalone, traduzioni
IT del dashboard, contenuti/procedure parziali, metadata persona/JTBD/outcome
non derivati dalla spec, migrazione di URL/schema e distribuzione marketplace.
MCP Apps embedded non e' implementato. Il test live non ha esercitato VS Code,
il collegamento Copilot Studio, `native-mcp`, backend external o auth Entra.

La prova cloud e' stata orchestrata dall'assistente tramite CLI installata;
non e' una nuova conversazione autonoma del plugin in Copilot. La precedente
prova conversazionale Copilot CLI rimane offline e assistita dal runner.
Ricevute e dashboard locali non sono un inventario autorevole dello stato Azure.

## Candidato 1.2.0: chiusura funzionale

Questo aggiornamento riguarda il codice locale **1.2.0, non ancora pubblicato**.
Non trasferisce al nuovo wheel le prove cloud del precedente 1.1.1 r3/r4.
Il collaudo finale deve registrare l'hash del candidato unico e l'esito distinto
di ogni percorso, secondo [la matrice E2E](e2e-testing.md#single-artifact-acceptance-for-120).

| Righe | Stato aggiornato nel codice installabile | Limite da mantenere esplicito |
|---|---|---|
| 1, 4 | Ripristinati livello di realismo per API, pre-mortem, questionari e contenuti di handover | La procedura guida il modello, non certifica automaticamente la semantica business |
| 11 | Libreria originale opzionale: due scenari, cinque API, diciotto tool; `examples` e `import-example`; catalogo builtin completo | Nessun cliente attivato dalla consultazione; `import-sample` resta lo starter neutro distinto |
| 18 | `provision` standalone: nuovo APIM pubblico Consumption o Basic v2 in resource group esistente, preview/apply espliciti e ricevuta di creazione | Non e' provisioning completo di piattaforma; nessun `retire-gateway` eseguibile o collaudo live implicito |
| 25 | Aggiornamento e riconciliazione del cliente restano nel deploy selettivo | Il collaudo live del candidato deve includere aggiunta, rename e rimozione, non soltanto una prima creazione |
| 28, 29 | Native mock opt-in con `--exercise-mock`; Entra/dual espliciti; fixture reali selezionate per operation/example con preview e conferma | Entra richiede chiave piu' bearer; payload nativi non riconosciuti falliscono; prove offline non certificano il provider reale |
| 32 | Persona/JTBD/outcome proiettati dal frontmatter dichiarato della spec, ricercabili in MCP/dashboard; contesti di clienti distinti preservati | Niente inferenza NLP opaca; override editoriali espliciti e conflitti segnalati |
| 33 | Ripristinate traduzioni IT delle etichette statiche e operative e dei fallback del dashboard | Non e' traduzione automatica degli example o dei dati business |
| 36, 40 | Bootstrap con checksum, runtime dedicato, export locale del plugin e upgrade affiancato senza sovrascrivere dati/installazione precedente | Python e dipendenze devono essere disponibili tramite TLS verificato o wheelhouse approvato; nessuna attivazione automatica dell'host |
| 41 | Procedura collega ripetibile e pacchetto locale; nessuna release/marketplace pubblicata da questa modifica | VS Code reale osservato senza login Copilot: conversazione E2E ancora non certificata |
| 43 | Builder di release e smoke wheel/sdist sullo stesso wheel e hash; CI Windows/Linux conserva il candidato solo dopo smoke riuscito | Artefatti CI offline, non deploy Azure o pubblicazione automatica di release |

Restano invariati i **14 tool, 7 resource e 3 prompt** del companion.
`provision`, `examples` e `import-example` estendono la CLI, non introducono
un esecutore arbitrario nel MCP. La dashboard rimane nel browser, non MCP Apps.

**Accettazione da non confondere con il codice disponibile:** nuovo APIM,
native MCP reale, backend controllato, Entra e Copilot Studio richiedono target,
identita' e prove specifiche. Il contesto proposto non e' un'ispezione e una
richiesta di autorizzazione rimasta senza risposta non autorizza nuovi target.
Il ritiro del cliente conserva APIM; per un gateway usa-e-getta va concordata
prima una procedura separata dell'operatore.
