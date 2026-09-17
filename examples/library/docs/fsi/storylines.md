# FSI Agent Suite — Storyline demo (Fase 0)

Scenario deterministico per il processo **RM-360**.
Riferimento narrativo della libreria installata, non una configurazione cliente
attiva. Dopo l'import consultare `../example-reference.md` e `import-map.json`
per i nomi API/tool prefissati. Le date sono uno snapshot fittizio dichiarato.
Metodo: universo narrativo fisso, dati coerenti tra i tool,
una "tensione" che guida la conversazione, decisioni defer compliance-driven,
input-chiave documentati nelle description. Questi dati diventeranno gli
`example` dei contratti `apis/fsi-*`; le regole qui descritte diventeranno
gli `x-mock`.

## Universo condiviso

- **Banca**: Banca Aurora (fittizia)
- **Cliente**: **Molino Ferrari S.r.l.** — `CUST-FSI-0042`, segmento SME,
  settore agroalimentare, cliente da 12 anni
- **Contatto**: Giulia Ferrari, Amministratrice Delegata
- **Utente dell'agente**: il Relationship Manager (persona interna)
- **Il filo che lega i due processi**: la domanda di finanziamento
  `LN-2026-0187` (€120.000, nuova linea di confezionamento) è **in
  underwriting, sospesa per un documento mancante** — la si incontra sia
  dal lato RM (alert nel 360) sia dal lato Loan (stato pratica).

---

## Storyline 1 — RM-360 ("preparami l'incontro con Molino Ferrari")

**Contratti**: `apis/fsi-crm`, `apis/fsi-core-banking`,
`apis/fsi-monitoring`, `apis/fsi-compliance`.

**La situazione**: il RM ha un incontro con Giulia Ferrari domani alle 10.
Chiede all'agente di prepararlo.

**Cosa scoprono i tool (dati fissi):**

| Tool (operationId) | Cosa restituisce nello scenario |
|---|---|
| `get-customer-profile` | Anagrafica, segmento SME, rating interno BB+, **KYC refresh in scadenza il 2026-08-20** (tensione #1), consensi ok |
| `get-customer-positions` | 2 conti (operativo €38.400, deposito €95.000), finanziamento esistente residuo €80.000, polizza aziendale; **domanda LN-2026-0187 in corso** |
| `get-customer-interactions` | Ultimo incontro 2026-06-12 (rinnovo fido), telefonata 2026-07-24 sui tempi del finanziamento (Giulia "preoccupata per i tempi"), **incontro fissato 2026-08-03 10:00** |
| `get-customer-alerts` | Alert cash-flow: incassi -18% ultimo trimestre sul conto operativo; alert documentale: manca la situazione contabile per LN-2026-0187 |
| `assess-commercial-eligibility` | **decision: defer** — "KYC in scadenza + pratica di finanziamento sospesa: completare prima questi due punti. Azione consigliata: preparare proposta di cash management (incassi digitali), NON proporre nuovi prodotti di investimento" |
| `create-follow-up-task` (SCRITTURA, Idempotency-Key) | Crea il task post-incontro (es. "raccogliere situazione contabile aggiornata") → 202, `TASK-2026-0311` |
| `get-commercial-exception` | Esito di un'eccezione richiesta: `EXC-*` → **pending-approval** con SLA (in mock la decisione non arriva; un eventuale backend esterno separato potrà fornire approved/denied con motivazione) |
| `screen-counterparty` | Screening controparti: "Molino Ferrari" → **clear**; il nuovo distributore estero **"Eastbridge Trading FZE"** (fittizio) → **potential-match** su liste marcate "(demo)" → l'agente propone l'escalation a compliance. Le liste reali richiedono un'integrazione separata, non inclusa |

**Il momento demo**: il RM chiede *"posso proporgli il nuovo pacchetto
investimenti?"* → l'agente, via `assess-commercial-eligibility`, risponde **no,
prima KYC e documento mancante** — e propone l'azione giusta. Governance
dimostrata, upsell rimandato con motivazione.

**Regole x-mock**: `customerId` che inizia per `CUST-` → dati scenario;
altrimenti → 404 problem+json. POST senza Idempotency-Key → 400.
Screening: `name` contiene "eastbridge" → potential-match, altrimenti clear.

---

## Perimetro dell'esempio

**La specifica di dettaglio (user stories, API, strutture dati, mapping
Dataverse, criteri di accettazione) è in [rm360-spec.md](rm360-spec.md)** — questo
documento resta la fonte della narrazione.

- **Architettura allineata al diagramma Microsoft "Empower relationship
  managers"**: un contratto per SISTEMA di record, `mcpExposure.mode: perApi`
  → 4 MCP server (`fsi-crm`, `fsi-core-banking`, `fsi-monitoring`,
  `fsi-compliance`), orchestratore + 3 task agent in Copilot Studio.
  Il perimetro RM-360 usa i 12 tool dei quattro contratti presenti.
- **Tutti i mock**: nessuno stato, nessun calcolo — snapshot coerente.
  Le scritture con 202 sono già disegnate per un futuro backend stateful.
- **Dataverse (CRM, stadio 1) è fuori dal perimetro del kit**: il contratto
  `fsi-crm` + la tabella di mapping nella spec sono l'interface agreement.
- Annotare in ogni contratto `x-derived-from` (dominio BIAN sorgente).
- Valori demo nelle description: `CUST-FSI-0042`, `LN-2026-0187`.
