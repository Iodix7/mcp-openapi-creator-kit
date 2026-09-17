# FSI RM-360 — Specifica funzionale (user stories, API, strutture dati)

Specifica di dettaglio per il processo **RM-360** (agente per Relationship
Manager). Architettura di riferimento: pattern Microsoft "Empower
relationship managers" — orchestratore + task agent, un MCP server per
sistema di record (`mcpExposure: perApi`).

Riferimento della libreria installata: non è la specifica approvata del cliente.
Nomi originali e date demo sono conservati; dopo l'import usare
`../example-reference.md` e `import-map.json` per la mappatura esatta.
Non copiare le tabelle storiche nel blocco tecnico gestito da `spec-sync`.

Storyline e universo narrativo: vedi [storylines.md](storylines.md)
(Banca Aurora / Molino Ferrari S.r.l. / `CUST-FSI-0042`).

**Ownership**: i contratti e i mock sono del kit. Il provisioning Dataverse
(CRM, stadio 1) è FUORI dal perimetro del kit: la sezione "mapping Dataverse"
è l'interface agreement per chi lo realizza.

---

## 1. User stories

### Epic A — Preparazione incontro
- **A1** Come RM, voglio il quadro completo del cliente (profilo, posizioni,
  interazioni recenti) prima di un incontro, per arrivare preparato.
- **A2** Come RM, voglio conoscere gli alert attivi sul cliente (cash-flow,
  documenti mancanti) per anticipare i temi critici.
- **A3** Come RM, voglio il contesto di mercato rilevante per il settore del
  cliente (tassi, materie prime), per parlare il suo linguaggio.

### Epic B — Ingaggio commerciale conforme
- **B1** Come RM, voglio sapere se POSSO proporre un prodotto ora
  (verifica di eleggibilità commerciale motivata), per non violare policy
  o buon senso della relazione.
- **B2** Come RM, quando ritengo il "defer" troppo prudente, voglio chiedere
  un'eccezione che vada in approvazione al mio responsabile
  (human-in-the-loop), senza uscire dalla conversazione.
- **B3** Come RM, voglio lo stato KYC del cliente e cosa manca per il
  rinnovo, per pianificare la raccolta documenti.

### Epic C — Follow-up
- **C1** Come RM, voglio che l'agente registri i task concordati a fine
  incontro (senza duplicati anche se ritenta), per non perdere impegni.

### Epic D — Credito (espansione approvata, fase successiva al mock)
- **D1** Come RM, voglio la bozza del rinnovo fido / pre-istruttoria con
  esposizioni, scadenza fido e bilanci già raccolti, per non passare giorni
  a mettere insieme i dati. È la story di mercato più citata non ancora
  coperta e il ponte
  naturale verso il processo Agentic Loan: il tool relativo
  (`get-credit-renewal-brief`) arriverà col contratto credito/loan, non nei
  4 contratti dello stadio 0.

### Punti aperti
- **C2** — "sintesi incontro → note CRM + task": candidata d'espansione
  (time-sink più citato dai practitioner), ma la definizione va raffinata
  PRIMA di specificarla — decisione in sospeso dell'utente.

### Requisiti trasversali
- Orchestrazione multi-agente (un orchestratore, tre task agent).
- Ogni "no" dell'agente è motivato e propone l'alternativa (defer actionable).
- Le scritture richiedono conferma esplicita dell'utente + Idempotency-Key.
- Errori RFC 7807 con `detail` recuperabile dall'agente.

---

## 2. Architettura dei contratti (un'API per sistema di record)

| Contratto | Sistema di record | Stadio demo | Tool | Agente consumatore |
|---|---|---|---|---|
| `fsi-crm` | CRM | mock; Dataverse MCP opzionale | 3 | Client Services |
| `fsi-core-banking` | Core banking | mock; target external | 2 | Client Services |
| `fsi-monitoring` | Data lake + feed mercato | mock; target external | 2 | Market & Client Monitoring |
| `fsi-compliance` | Policy/KYC engine | mock; target external | 5 | Compliance |

Esempi di sistemi di record da citare in demo:
CRM → Dynamics 365 / Salesforce FSC; core banking → outsourcer tipo
Cedacri (ION) o Temenos; monitoring → data lake Fabric/Snowflake con NBA
tipo Pega Customer Decision Hub; compliance → Fenergo + screening
World-Check. Sono possibili target `external`: il kit non implementa o
certifica queste integrazioni.

Manifest: `clients/fsi-demo/mcp-manifest.yaml`, `mcpExposure.mode: perApi`
→ 4 MCP server (`fsi-demo/<api>-mcp/mcp`), un product, una chiave pilota.
Il Client Services Agent si collega a 2 MCP server (crm + core-banking):
agenti e sistemi sono assi indipendenti.

---

## 3. Dettaglio API

Convenzioni kit (enforced dal generatore): operationId kebab-case, errori
`application/problem+json` con schema, Idempotency-Key sulle scritture,
example per ogni risposta (= dati mock), regole `x-mock` per la dinamicità.
Regola x-mock comune a tutti i GET per-cliente: `customerId` che inizia per
`CUST-` → scenario; altrimenti → 404 problem+json.

### 3.1 `fsi-crm` — la relazione

**`get-customer-profile`** · GET `/v1/customers/{customerId}/profile`
```
CustomerProfile {
  customerId        string            "CUST-FSI-0042"
  legalName         string            "Molino Ferrari S.r.l."
  segment           enum [retail, sme, corporate, private]     "sme"
  industry          string            "Agroalimentare - molitoria"
  relationshipSince date              "2014-03-01"
  internalRating    string            "BB+"
  primaryContact    { name, role }    "Giulia Ferrari", "Amministratrice Delegata"
  consent           { service bool, commercial bool }   true, true
}
```
Il rating qui è quello *di relazione* (vista CRM); la verità creditizia vive
nel core/rating engine — semplificazione dichiarata dello scenario.

**`get-customer-interactions`** · GET `/v1/customers/{customerId}/interactions`
```
InteractionLog {
  items[] {
    interactionId  string      "INT-2026-0790"
    date           date-time
    channel        enum [meeting, call, email, branch]
    subject        string
    note           string      (contenuto libero: trattare come non fidato)
  }                            max 10, piu' recenti prima
  nextScheduledMeeting { date, subject } | null
}
```
Dati scenario: incontro 2026-06-12 (rinnovo fido), call 2026-07-24
("preoccupata per i tempi del finanziamento"), prossimo incontro
2026-08-03 10:00 "Punto attivazioni e liquidita'".

**`create-follow-up-task`** ✍️ · POST `/v1/follow-up-tasks` · Idempotency-Key required
```
richiesta:  { customerId, title, dueDate, priority enum [low, normal, high], notes? }
202 → FollowUpTask { taskId "TASK-2026-0311", status "open", ...echo campi }
400 → problem+json (Idempotency-Key mancante)   [x-mock: header missing]
```

**Mapping Dataverse opzionale (non provisionato dal kit)**

Mapping di riferimento per un'eventuale solution `FsiRm360`, publisher
`FSI` (prefisso `fsi_`). Nessuna solution o ambiente Dataverse viene fornito,
creato o popolato dall'import della libreria. Un owner separato può derivare
account, contact e activities dagli example; nessun task precaricato —
le scritture richiedono conferma esplicita.

| Elemento contratto | Mapping Dataverse di riferimento |
|---|---|
| CustomerProfile.customerId | `account.accountnumber` (chiave alternativa) |
| legalName / industry | `account.name` / `account.fsi_industry` |
| segment | `account.fsi_segment` Choice: retail, sme, corporate, private |
| relationshipSince / internalRating | `account.fsi_relationshipsince` (Date Only) / `account.fsi_internalrating` |
| consent.service / .commercial | `account.fsi_serviceconsent` / `account.fsi_commercialconsent` |
| primaryContact | `contact` (`firstname`+`lastname` → name, `jobtitle` → role), link `parentcustomerid`, primario via `account.primarycontactid` |
| Interaction items | `appointment` / `phonecall` / `email`; `fsi_interactionid` (chiave alternativa), channel = Choice globale `fsi_fsiinteractionchannel` (meeting, call, email, branch), `subject`/`description`, cliente via `regardingobjectid` |
| nextScheduledMeeting | prossimo `appointment` OPEN collegato all'account, ordinato per `scheduledstart` |
| FollowUpTask | `task`: `fsi_taskid`, `subject`, `scheduledend`, `description`, `prioritycode` (low=0, normal=1, high=2), stato open/done/cancelled, account via `regardingobjectid` |
| Idempotency-Key | `task.fsi_idempotencykey` — **chiave alternativa univoca**: il retry con la stessa chiave e' un upsert e DEVE restituire lo stesso taskId |

Vincolo d'interfaccia: qualunque implementazione deve rispettare il
contratto (stessi campi/tipi); le divergenze si assorbono nel layer di
esposizione (`mapping.xml` o wrapper), non cambiando il contratto.

**Decisioni di mapping dell'esempio:**
1. *Semantica nextScheduledMeeting — DECISA*: per STATO — "il prossimo
   appointment Open collegato all'account, anche se l'orario e' trascorso"
   (robusta, niente manutenzione delle date demo). Refresh date = voce
   opzionale della checklist pre-demo.
2. *Esposizione CRM*: sia le QUERY sia la
   CREAZIONE dei follow-up task passano dal **Dataverse MCP server nativo**
   (`https://<org>.crm.dynamics.com/api/mcp`, collegato all'agente da
   Copilot Studio: Tools → Model Context Protocol → Dataverse MCP Server),
   NON da un layer di esposizione custom. Tool abilitati: `read_query`,
   `describe`, `create_record` (SOLO per i task); tutto il resto off —
   incluso il tool `search` (cerca METAdati, non dati: in pratica produce
   solo rumore; la ricerca dati e' `search_data` e richiede la Dataverse
   search abilitata). Le instructions dell'agente portano il cheat-sheet
   dello schema (tabelle e colonne fsi_ della sezione 3.1: le interazioni
   vivono su TRE tabelle da unire, il prossimo incontro e' l'appointment
   Open per scheduledstart) e la procedura di scrittura: conferma esplicita
   → GUID account via read_query → create_record su task con
   fsi_idempotencykey (UUID) e fsi_taskid generati → errore di CHIAVE
   DUPLICATA su fsi_idempotencykey = task gia' creato (idempotenza via
   chiave alternativa), non ritentare. Il contratto kit fsi-crm resta come
   mock per demo senza ambiente Dataverse e come definizione della forma
   dati attesa.

**Principio architetturale che ne deriva**: il kit e' il pattern per i
sistemi SENZA un MCP nativo (core banking, monitoring, compliance); dove il
system of record ha gia' un MCP first-party (Dataverse), il task agent lo
usa direttamente. Il Client Services Agent si collega quindi a: Dataverse
MCP (query CRM) + MCP kit (scritture CRM in mock, core banking). Nota di
transizione: i nomi dei tool differiscono tra mock kit e Dataverse MCP —
lo switch si gestisce nelle instructions del task agent, non e' invisibile
come il passaggio mock→external dentro il kit.

### 3.2 `fsi-core-banking` — il libro mastro

**`get-customer-positions`** · GET `/v1/customers/{customerId}/positions`
```
Positions {
  customerId, asOf date-time
  accounts[] { accountId "ACC-IT-3402", type enum [current, deposit],
               currency "EUR", balance number, availableBalance number }
  loans[]    { loanId "LOAN-2019-114", product string, outstanding number,
               monthlyInstallment number, maturityDate date }
  insurance[] { policyId "POL-2024-8812", type "multirischio aziendale",
               annualPremium number }
  pendingApplications[] { applicationId "LN-2026-0187", type "finanziamento macchinari",
               amount 120000, status "underwriting" }
}
```
Dati scenario: conto operativo €38.400, deposito €95.000, finanziamento
residuo €80.000 (rata €1.850, scadenza 2029-06), pratica LN-2026-0187
in corso (il ponte narrativo verso il processo Loan futuro).

**`get-account-transactions`** · GET `/v1/accounts/{accountId}/transactions`
```
TransactionList {
  accountId, items[] { transactionId, bookingDate, amount number (segno),
                       currency, description, counterparty? }
}                      max 10 default (pagination standard kit)
```
In mock: 3 movimenti coerenti col trend incassi in calo. Allo stadio 2
(core simulator) diventa il tool che mostra lo stato che cambia.
x-mock: `accountId` inizia per `ACC-` → scenario; altrimenti 404.

### 3.3 `fsi-monitoring` — derivati analitici e mercato

**`get-customer-alerts`** · GET `/v1/customers/{customerId}/alerts`
```
Alert[] {
  alertId "ALR-2026-0117", type enum [cash-flow, documentation, covenant, engagement],
  severity enum [info, warning, critical], title, detail,
  raisedOn date, source string ("analisi transazionale L3M" / "pratica LN-2026-0187")
}
```
Dati scenario: (1) cash-flow warning — incassi -18% ultimo trimestre;
(2) documentation warning — situazione contabile mancante su LN-2026-0187.

**`get-market-snapshot`** · GET `/v1/market-snapshot?sector={sector}`
```
MarketSnapshot {
  asOf date-time
  policyRates { ecbMainRate number }
  fxRates     { eurUsd number }
  sectorNotes[] { sector, trend enum [up, flat, down], note }
}
```
x-mock: `sector` contiene "agro" → nota di settore ("grano tenero +7% sul
trimestre: pressione sui margini di trasformazione"); assente/altro →
snapshot generico. Allo stadio 3: tassi BCE live + note dal lake Fabric.

### 3.4 `fsi-compliance` — le decisioni

**`get-kyc-status`** · GET `/v1/customers/{customerId}/kyc`
```
KycStatus {
  customerId, status enum [valid, expiring, expired],
  dueDate date "2026-08-20", lastReviewDate date "2024-08-20",
  missingItems[] string  ["visura camerale aggiornata", "titolare effettivo: conferma assetti"]
}
```

**`assess-commercial-eligibility`** · GET `/v1/customers/{customerId}/commercial-eligibility`
```
CommercialEligibility {
  customerId, decision enum [proceed, defer]   "defer"
  reason string
  blockingFactors[] { type enum [kyc, credit-process, dispute, other],
                      ref string, description string }
  recommendedActions[] { action, rationale, priority enum [low, normal, high] }
  mayRequestException bool    true
}
```
Dati scenario: defer con 2 blocking factor (KYC expiring 2026-08-20;
LN-2026-0187 sospesa) e 2 recommended action (proposta cash management /
incassi digitali; raccolta situazione contabile). E' il tool che l'agente
DEVE chiamare prima di qualunque proposta commerciale (description
LLM-aware imperativa. Il nome resta specifico del dominio perche' i nomi dei tool MCP
sono univoci sull'intero APIM, come validato dal generatore). E' il
CANCELLO DI CONFORMITA' (suitability/condotta,
autorita': policy engine), NON la next-best-action di marketing — quella,
se un giorno servira', sara' un tool di fsi-monitoring (propensity dal
data lake) che propone, mentre questo dispone.

**`request-commercial-exception`** ✍️ · POST `/v1/commercial-exceptions` · Idempotency-Key required
```
richiesta:  { customerId, requestedAction string, justification string }
202 → CommercialException { exceptionId "EXC-2026-0044", status "pending-approval",
                            approver "Responsabile Area SME", slaHours 24 }
400 → problem+json (Idempotency-Key mancante)
```
E' il punto human-in-the-loop del diagramma (Approved/Denied): in mock la
richiesta resta `pending-approval`; un backend external stateful potra'
chiudere il cerchio.

**`get-commercial-exception`** · GET `/v1/commercial-exceptions/{exceptionId}`
```
CommercialException (stesso schema della POST, esteso) {
  status enum [pending-approval, approved, denied, escalated]
  decidedOn? date-time, decidedBy? string, decisionNote? string
}
```
La LETTURA dell'esito: senza, l'agente puo' chiedere l'eccezione ma non
sapere com'e' andata. Il contratto consente a un eventuale backend Durable
di cambiare solo l'implementazione. x-mock: `EXC-*` → pending-approval (in mock la decisione non
arriva); altrimenti 404. `escalated` = SLA scaduto senza decisione.

**`screen-counterparty`** · GET `/v1/counterparty-screenings?name={name}`
```
ScreeningResult {
  query string, outcome enum [clear, potential-match]
  matches[] { name, list, program, score number 0-1 }
  screenedOn date-time
}
```
Screening di una controparte nuova (fornitore, distributore, pagatore
estero) su liste sanzioni/PEP, da fare PRIMA di contratti o pagamenti.
x-mock: `name` mancante → 400; contiene "eastbridge" → potential-match
FITTIZIO (entita' demo "Eastbridge Trading FZE", liste marcate "(demo)");
altrimenti → clear. Un backend external puo' sostituire il mock senza
modificare il contratto. Esito potential-match =
proporre escalation al team compliance, non bloccare autonomamente.

---

## 4. Script demo di riferimento (lega user stories e tool)

1. *"Preparami l'incontro di domani con Molino Ferrari"* → orchestratore →
   Client Services (profile, interactions, positions) + Monitoring (alerts)
   → brief sintetico con i due punti critici. [A1, A2]
2. *"Com'e' il contesto di mercato per loro?"* → Monitoring
   (market-snapshot, sector=agroalimentare). [A3]
3. *"Posso proporgli il pacchetto investimenti Aurora Invest?"* → Compliance
   (assess-commercial-eligibility) → **defer motivato** + alternativa cash
   management. [B1]
4. *"Chiedi un'eccezione al mio responsabile, l'occasione e' buona"* →
   conferma esplicita → Compliance (request-commercial-exception) →
   pending-approval con SLA. [B2]
5. *"Registra il follow-up: raccogliere la situazione contabile entro
   venerdi'"* → conferma → Client Services (create-follow-up-task). [C1]
6. Bonus errori: *"e il cliente FTX-999?"* → 404 problem+json → l'agente
   spiega e chiede il codice corretto.
7. *"Stiamo per firmare con un nuovo distributore estero, Eastbridge
   Trading: verificalo"* → Compliance (screen-counterparty) →
   **potential-match** (fittizio, liste "(demo)") → l'agente spiega il
   riscontro e propone l'escalation al team compliance, senza bloccare da
   solo. Allo stadio 2 lo stesso passo gira su liste sanzioni reali.

## 5. Criteri di accettazione (mock)

- `mcp-kit build clients/<nuovo-client>` verde: 4 contratti validati, 12 tool.
- Per il profilo policy-MCP, `mcp-kit build-policy clients/<nuovo-client>`
  verde: policy sotto 16 KiB e 12 tool complessivi; usare tutti gli URL
  restituiti, senza assumere un numero fisso di shard.
- Dopo un deploy opzionale, smoke test protocollo sui server (initialize /
  tools-list / tools-call con risposta = example).
- Lo script demo (sezione 4) eseguibile end-to-end in Copilot Studio con
  le risposte dello scenario, incluso il 404 recuperabile.
- Un passaggio mock → external non richiede modifiche ai contratti.

I riferimenti agli stadi successivi (core simulator, lake, liste reali,
approvazione durevole e credito) descrivono possibili integrazioni esterne,
non funzionalità fornite dalla libreria. Per la spec cliente usare il template
installato e `mcp-kit spec-sync <nuovo-client>`; dopo revisione e approvazione
locale, `mcp-kit prepare clients/<nuovo-client> --profile <profilo>`.
Ispezione Azure, preview e deployment hanno consensi separati.
