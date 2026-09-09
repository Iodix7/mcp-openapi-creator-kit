# Pilot AI Gateway: esecuzione locale e gate live

La fixture è fittizia: non è una configurazione operativa client, non viene
caricata da azd e non crea risorse Azure.

```powershell
python tools\run-target-pilot.py ai-gateway-preview
python -m pytest tools\tests\test_target_pilots.py -q --basetemp=.pilot-tests-gateway
```

Output persistente: `clients/sample/generated/pilot-ai-gateway/`, con OpenAPI
selezionato e `ai-gateway-plan.json`. La selezione del pilot contiene soltanto
`get-customer-context`; il manifest operativo non viene modificato.

Il test consuma la proiezione in un adapter MCP locale in-process:
initialize/discovery/tool call → HTTP loopback → risposta del contratto.
Verifica anche il rifiuto HTTP senza credenziale. **L'adapter non è un
emulatore del tier** e non dimostra protocollo management, namespace,
autenticazione gateway, policy o funzionamento live.

Un secondo test controlla i report del companion e del dashboard, verificando
che non scrivano file e che mantengano le protezioni loopback/CSP.

Prima del live:

1. Approvare il contesto Azure completo: account, tenant, subscription,
   ambiente azd, resource group, regione, gateway profile e gateway target.
   Usare un gateway dedicato per isolare la chiave gateway-wide.
2. Verificare disponibilità preview in eastus2 o swedencentral.
3. Usare il portale ufficiale, non payload REST inventati. `--apply` è
   intenzionalmente bloccato: il contratto management non è verificato.
4. Collegare un REST/MCP già funzionante. Example e `x-mock` non sono un
   backend; OAuth interattivo non è client credentials.
5. Configurare autenticazione backend e chiave runtime in secret store.
   Con un consumer MCP compatibile verificare discovery/call, richieste senza
   chiave, namespace effettivi, errori e osservabilità.
6. Registrare evidenza e piano di rollback; eliminare risorse/chiavi solo dopo
   nuova approvazione con scope esplicito.

Non ci sono identità, subscription o resource group upstream ereditati.
Queste verifiche locali non effettuano modifiche Azure.
