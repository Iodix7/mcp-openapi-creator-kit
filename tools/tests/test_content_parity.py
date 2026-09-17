"""Execute the shipped dashboard JS: translations must reach rendered output."""
from html.parser import HTMLParser
import json
import shutil
import subprocess

import pytest

from mcp_openapi_creator_kit.assets import asset_text
from mcp_openapi_creator_kit.catalog import render_index


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes, self.scripts = [], []
        self.in_script = False
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.in_script = True
            self.scripts.append("")
        if tag == "select":
            self.select = attrs.get("id")
        if "id" in attrs or "class" in attrs or any(key.startswith("data-") for key in attrs) or tag in {"option", "small"}:
            self.nodes.append({"tag": tag, "attrs": attrs, "select": self.select if tag == "option" else None})

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False
        if tag == "select":
            self.select = None

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data


# No browser/DOM package dependency: the actual installed JS runs against a
# deterministic DOM surface, and assertions inspect rendered HTML and events.
JS_RUNNER = r"""
(async()=>{
const fs=require("fs"),vm=require("vm");
const input=JSON.parse(fs.readFileSync(0,"utf8"));
class Element {
  constructor(tag,attrs={}) {
    this.tag=tag;this.attrs=attrs;this.dataset={};this.children=[];
    this._html="";this._text="";this.value=attrs.value||"";this.open=false;
    for(const [k,v] of Object.entries(attrs))if(k.startsWith("data-"))this.dataset[k.slice(5)]=v;
    this.classList={toggle:()=>{}};
  }
  setAttribute(k,v){this.attrs[k]=v}
  get options(){return this.children}
  set textContent(v){this._text=String(v);this._html="";this.children=[]}
  get textContent(){return this._text}
  set innerHTML(v){this._html=String(v);this._text="";this.children=parse(String(v))}
  get innerHTML(){return this._html}
  insertAdjacentHTML(_,v){this.children.push(...parse(v))}
  appendChild(e){this.children.push(e)}
  querySelector(selector){return this.children.find(e=>match(e,selector))}
  showModal(){this.open=true}
  close(){this.open=false}
}
function parse(html){
  return [...html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)].map(m=>{
    const attrs={};
    for(const a of m[2].matchAll(/([\w-]+)="([^"]*)"/g))attrs[a[1]]=a[2];
    return new Element(m[1],attrs);
  });
}
function match(e,s){
  if(s===".brand small")return e.tag==="small";
  if(s.startsWith("."))return (e.attrs.class||"").split(" ").includes(s.slice(1));
  if(s.startsWith("["))return Object.hasOwn(e.attrs,s.slice(1,-1));
  return e.tag===s;
}
const nodes=[];
for(const n of input.nodes){
  const e=new Element(n.tag,n.attrs);
  if(n.select)nodes.find(x=>x.attrs.id===n.select).children.push(e);else nodes.push(e);
}
const body=new Element("body"),html=new Element("html");
const all=()=>[...nodes,...nodes.flatMap(e=>e.children),...body.children,...body.children.flatMap(e=>e.children)];
const document={
  body,documentElement:html,title:"",
  getElementById:id=>all().find(e=>e.attrs.id===id),
  querySelectorAll:selector=>all().filter(e=>match(e,selector)),
  createElement:tag=>new Element(tag)
};
const context=vm.createContext({
  document,window:{location:{search:""},matchMedia:()=>({matches:false})},
  URLSearchParams,console,navigator:{clipboard:{writeText:async text=>{context.copied=text}}}
});
for(const script of input.scripts)vm.runInContext(script,context);
function evalJS(code){return vm.runInContext(code,context)}
function snapshot(){
  const ids=["catalog-label","filter-toggle","workflow-button","workflow-title","profiles-title","targets-button","stats","result-list","result-count","detail","client-list","profiles-grid","workflow-list","workflow-summary","workflow-warning"];
  const state={};
  for(const id of ids){const e=document.getElementById(id);state[id]=e.innerHTML||e.textContent}
  state.lang=html.lang;state.title=document.title;
  state.search=document.getElementById("search").placeholder;
  state.searchAria=document.getElementById("search").attrs["aria-label"];
  state.close=document.getElementById("workflow-close").attrs["aria-label"];
  state.mockOptions=document.getElementById("mock-filter").options.map(e=>e.textContent);
  state.profileOptions=document.getElementById("profile-filter").options.map(e=>e.textContent);
  state.targets=body.children[0].innerHTML;
  state.labels=nodes.filter(e=>e.dataset.i18n).map(e=>e.textContent);
  state.source=nodes.find(e=>e.tag==="small")?.textContent;
  state.detailAria=document.getElementById("detail").attrs["aria-label"];
  return state;
}
const out={en:snapshot()};
nodes.find(e=>e.dataset.lang==="it").onclick();
out.it=snapshot();
document.getElementById("workflow-button").onclick();
out.open=document.getElementById("workflow-dialog").open;
document.getElementById("workflow-close").onclick();
out.closed=!document.getElementById("workflow-dialog").open;
evalJS('tab="schemas";renderDetail()');out.schemas=document.getElementById("detail").innerHTML;
evalJS('tab="configuration";renderDetail()');out.configuration=document.getElementById("detail").innerHTML;
const copy=document.querySelectorAll("[data-copy]")[0];
if(copy){await copy.onclick();out.copied=context.copied;out.copyLabel=copy.textContent}
document.getElementById("method-filter").value="POST";
document.getElementById("method-filter").onchange();out.methodCount=document.getElementById("result-count").textContent;
document.getElementById("method-filter").value="";
document.getElementById("profile-filter").value="policy-mcp-consumption";
document.getElementById("profile-filter").onchange();out.profileCount=document.getElementById("result-count").textContent;
document.getElementById("search").value="__no_results__";
document.getElementById("search").oninput();out.empty=snapshot();
document.getElementById("search").value="";
document.getElementById("mock-filter").value="static";
document.getElementById("mock-filter").onchange();out.staticCount=document.getElementById("result-count").textContent;
document.getElementById("mock-filter").value="dynamic";
document.getElementById("mock-filter").onchange();
nodes.find(e=>e.dataset.lang==="en").onclick();out.enAgain=snapshot();
out.selectedFilter=document.getElementById("mock-filter").value;
out.translations=evalJS('({it:T.it,en:T.en})');
out.searches=(input.queries||[]).map(([language,query])=>{
  for(const id of ["profile-filter","method-filter","mock-filter"])document.getElementById(id).value="";
  nodes.find(e=>e.dataset.lang===language).onclick();
  document.getElementById("search").value=query;
  document.getElementById("search").oninput();
  return snapshot();
});
process.stdout.write(JSON.stringify(out));
})().catch(error=>{console.error(error);process.exitCode=1});
"""


def index(workflow=True):
    hostile = '<img src=x onerror="alert(1)"> & \' </script>'
    data = {
        "summary": {"scenarios": 1, "operations": 1, "schemas": 1},
        "profiles": [{
            "id": "policy-mcp-consumption", "label": {"en": "Policy profile", "it": "Profilo policy"},
            "gateway": "Consumption", "interface": "MCP", "fixedCost": False, "mockOnly": True,
            "supports": ["tools"],
        }],
        "clients": [{"id": "retail", "displayName": "Retail", "apis": ["care"], "exposure": {"mode": "perApi"}}],
        "targetCapabilities": {},
        "scenarios": [{
            "id": "care", "title": {"it": "Assistenza", "en": "Care"}, "description": hostile,
            "domain": "Care", "tags": [], "mock": {"type": "dynamic", "ruleCount": 2},
            "compatibility": {"policy-mcp-consumption": {"supported": True, "servers": [{"usagePercent": 12}]}},
            "usedBy": [], "manifestSnippet": "apis: []",
            "schemas": [{"name": "Problem", "canonical": True, "schema": {"type": "object"}}],
            "operations": [{"method": "GET", "path": "/v1/test", "operationId": "get-test",
                "description": hostile, "summary": "Test", "parameters": [
                    {"name": "id", "in": "query", "required": True, "schema": {"type": "string"}}],
                "responses": [{"status": "200", "description": "OK", "examples": [{"name": "default", "value": hostile}]}]}],
        }],
        "workflow": [],
    }
    if workflow:
        data["workflow"] = [{
            "client": None, "status": "needs-input", "reason": hostile,
            "profile": None, "provisionalProfile": None, "evidenceStatus": "missing",
            "evidence": {"target": {"subscription": hostile}, "facts": {}},
            "approvalStatus": "not-granted", "missingInputs": ["requires_mcp", "provisioning_target"],
            "blockers": [], "allowedActions": ["read-guidance", "provision-preview"], "checksPending": ["Contract validation"],
            "nextAction": hostile, "completion": "not-started",
            "steps": [{"id": "manifest", "status": "missing", "detail": hostile, "plan": {"target": hostile}}],
            "nextCommand": ["mcp-kit", "prepare", "clients/retail"],
            "nextInvocation": {"executable": "python", "arguments": ["-I"],
                               "effect": "provisioning-preview"},
            "currentStep": {"stage": "collect-context", "why": [hostile], "instructions": hostile,
                "consent": hostile, "completion": hostile,
                "source": {"asset": "skills/discovery.md", "version": "1", "resourceUri": "kit://guidance"}},
        }]
    return data


def execute(data, queries=()):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for isolated dashboard rendering tests")
    page = Page(render_index(data))
    result = subprocess.run(
        [node, "-e", JS_RUNNER], input=json.dumps({"nodes": page.nodes, "scripts": page.scripts, "queries": queries}),
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_actual_italian_english_rendering_and_state_switch():
    result = execute(index())
    it, en = result["it"], result["en"]
    assert en["catalog-label"] == "Capability catalog"
    assert it["catalog-label"] == "Catalogo delle funzionalità"
    assert it["lang"] == "it" and en["lang"] == "en"
    assert it["title"].endswith("Catalogo delle funzionalità")
    assert it["search"] == "Scenario, strumento, schema..."
    assert it["searchAria"] == "Cerca scenari"
    assert it["source"].startswith("Dati del cliente")
    assert it["detailAria"] == it["source"]
    assert it["mockOptions"] == ["Tutti", "Dinamico", "Statico"]
    assert it["profileOptions"] == ["Tutti", "Profilo policy"]
    assert {"Cerca", "Profilo", "Metodo", "Composizioni", "Istantanea del flusso · sola lettura"} <= set(it["labels"])
    assert "Parametri" in it["detail"] and "Risposte" in it["detail"]
    assert "<th>Nome</th>" in it["detail"] and "obbligatorio" in it["detail"]
    assert "Configurazione" in result["configuration"] and ">Copia</button>" in result["configuration"]
    assert result["copied"] == "apis: []" and result["copyLabel"] == "Copiato"
    assert result["methodCount"] == "0 risultati" and result["profileCount"] == "1 risultati"
    assert ">canonico</span>" in result["schemas"]
    assert "Solo mock" in it["profiles-grid"] and "Costo fisso" in it["profiles-grid"]
    assert "Destinazioni consumer · sola lettura" in it["targets"]
    for phrase in ("Cliente in bozza", "Informazioni richieste", "Profilo confermato",
                   "Non confermato", "Non selezionato", "Non concessa", "Informazioni mancanti",
                   "Prossima azione", "Gruppo di risorse", "Non fornito", "Argomenti del prossimo comando",
                   "Consenso richiesto", "Raccolta del contesto", "Validazione dei contratti",
                   "Piano di anteprima registrato", "Contesto per la creazione del gateway",
                   "Anteprima della creazione del gateway"):
        assert phrase in it["workflow-list"]
    assert "non è un&#39;approvazione del deployment" in it["workflow-warning"]
    assert result["open"] and result["closed"]
    assert result["empty"]["result-count"] == "0 risultati"
    assert result["staticCount"] == "0 risultati"
    assert result["selectedFilter"] == "dynamic"
    assert result["enAgain"]["catalog-label"] == en["catalog-label"]
    assert result["enAgain"]["mockOptions"] == ["All", "Dynamic", "Static"]
    assert "No optional target configured" not in it["targets"]


def test_rendered_untrusted_data_stays_escaped_in_both_languages():
    result = execute(index())
    for language in ("it", "en", "enAgain"):
        for field in ("detail", "workflow-list", "workflow-summary"):
            assert "<img src=x" not in result[language][field]
            assert "&lt;img src=x" in result[language][field]
        assert "skills/discovery.md" in result[language]["workflow-list"]
        assert "clients/retail" in result[language]["workflow-list"]
        assert "provisioning-preview" in result[language]["workflow-list"]
    # Source prose/JSON and exact command identifiers are intentionally not rewritten.
    assert "&lt;/script&gt;" in result["it"]["workflow-list"]


def test_empty_workflow_is_rendered_in_selected_language():
    result = execute(index(workflow=False))
    assert "No client workflow snapshot available." in result["en"]["workflow-list"]
    assert "Nessuna istantanea del flusso disponibile." in result["it"]["workflow-list"]
    assert "No client workflow" not in result["it"]["workflow-summary"]


def test_empty_customer_and_builtin_source_labels_are_translated():
    data = index(workflow=False)
    data["scenarios"] = []
    data["summary"] = {"scenarios": 0, "operations": 0, "schemas": 0}
    result = execute(data)
    assert "Nessun contratto cliente." in result["it"]["detail"]
    assert "No customer contracts yet." in result["en"]["detail"]
    assert "Nessun contratto cliente." in result["empty"]["result-list"]
    data["source"] = "builtin-starter-library"
    result = execute(data)
    assert result["it"]["source"] == "Libreria iniziale integrata — nessun cliente attivo"


def test_explicit_localized_scenario_metadata_is_searchable_in_both_languages():
    data = index()
    scenario = data["scenarios"][0]
    scenario.update({
        "title": {"it": "Sportello Imprese", "en": "Business Branch"},
        "persona": {"it": "Responsabile filiale", "en": "Branch manager"},
        "jobToBeDone": {"it": "Preparare appuntamenti", "en": "Prepare appointments"},
        "outcome": {"it": "Ridurre attese", "en": "Reduce waiting"},
    })
    queries = [(language, term) for language in ("it", "en") for term in (
        "Sportello Imprese", "Business Branch", "Responsabile filiale", "Branch manager",
        "Preparare appuntamenti", "Prepare appointments", "Ridurre attese", "Reduce waiting")]
    result = execute(data, queries)
    assert all(state["result-count"].startswith("1 ") for state in result["searches"])
    assert "<strong>Responsabile filiale</strong>" in result["it"]["detail"]
    assert "<strong>Prepare appointments</strong>" in result["en"]["detail"]
    assert "Scenari per cliente" not in result["it"]["detail"]


def test_shared_scenario_contexts_render_per_client_and_search_without_flattening():
    data = index()
    scenario = data["scenarios"][0]
    scenario["persona"] = "Editorial override"
    scenario["scenarioContexts"] = [{
        "client": "retail", "source": "docs/retail/spec.md",
        "title": {"it": "Sportello Imprese", "en": "Business Branch"},
        "persona": {"it": "Responsabile filiale", "en": "Branch manager"},
        "jobToBeDone": {"it": "Preparare appuntamenti", "en": "Prepare appointments"},
        "outcome": {"it": "Ridurre attese", "en": "Reduce waiting"},
    }, {
        "client": "bank<tenant>", "source": 'docs/<img src=x onerror="alert(1)">/spec.md',
        "title": {"it": "Rischio commerciale", "en": "Commercial risk"},
        "persona": {"it": "Analista credito", "en": "Credit analyst"},
        "jobToBeDone": {"it": "Verificare esposizioni", "en": "Review exposures"},
        "outcome": {"it": "Decisione <sicura>", "en": "Safe <decision>"},
    }]
    queries = [(language, term) for language in ("it", "en") for term in (
        "Sportello Imprese", "Business Branch", "Responsabile filiale", "Branch manager",
        "Preparare appuntamenti", "Prepare appointments", "Ridurre attese", "Reduce waiting",
        "Analista credito", "Credit analyst", "Review exposures", "Verificare esposizioni",
        "Decisione <sicura>", "Safe <decision>", "Editorial override")]
    result = execute(data, queries)
    assert all(state["result-count"].startswith("1 ") for state in result["searches"])
    for language in ("en", "it"):
        detail = result[language]["detail"]
        assert "Editorial override" in detail
        assert "docs/retail/spec.md" in detail
        assert "bank&lt;tenant&gt;" in detail and "bank<tenant>" not in detail
        assert "docs/&lt;img" in detail and "docs/<img" not in detail
        assert "<strong>Test</strong>" not in detail  # An operation summary is not a JTBD.
    assert "Scenari per cliente" in result["it"]["detail"]
    assert "Specifica di origine" in result["it"]["detail"]
    assert "Decisione &lt;sicura&gt;" in result["it"]["detail"]
    assert "Credit analyst" in result["en"]["detail"]
    assert "Client scenarios" in result["en"]["detail"]


def test_restored_discovery_and_handover_are_installed_procedures():
    discovery, handover = asset_text("skills/discovery.md"), asset_text("HANDOVER.md")
    excerpt, extended = discovery.split("<!-- kit-step:define-scenario -->", 1)[1].split(
        "<!-- /kit-step:define-scenario -->", 1)
    assert len(excerpt.strip()) < 4500
    assert "Stateful data layer" not in excerpt
    assert "Stateful data layer" in extended and "## Demo validation and pre-mortem checklist" in extended
    for phrase in ("data-derived", "per API", "Pre-mortem", "stateful", "Analytical",
                   "system", "Idempotency-Key", "first-party", "spec-sync"):
        assert phrase.lower() in discovery.lower()
    for phrase in ("anonymized real response", "DLP", "Residency", "per-API", "soft-delete",
                   "Exposure model", "pre-mortem", "dedicated installation", "import-example"):
        assert phrase in handover
    for text in (discovery, handover):
        assert "python tools/" not in text
        assert "pip install" not in text
