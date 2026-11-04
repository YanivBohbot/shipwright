# Shipwright Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construire Shipwright, un Deep Agent (LangChain) qui transforme un ticket Jira en Pull Request GitHub validée par un humain, en mobilisant toutes les briques de la doc Deep Agents.

**Architecture:** Un orchestrateur `create_deep_agent` délègue à 4 sous-agents (researcher, code_analyst, developer, reviewer) qui travaillent sur un clone du repo démo dans une sandbox. Seul l'orchestrateur écrit vers l'extérieur, via des outils composites gardés par HITL qui appellent GitHub MCP et Atlassian MCP derrière des interfaces (`JiraGateway`, `GitHubGateway`) remplaçables par des fakes pour les tests et évals. Mémoire et policies via `CompositeBackend` (sandbox par défaut + routes `StoreBackend`), persistance MongoDB, traces LangSmith, déploiement LangSmith Deployment.

**Tech Stack:** Python 3.12, uv, deepagents, langchain / langgraph, langchain-openai, langchain-mcp-adapters, GitHub MCP (remote HTTP), mcp-atlassian (stdio), langchain-pinecone, llama-cloud-services (LlamaParse), langchain-tavily, langgraph-checkpoint-mongodb, langgraph-store-mongodb, pymongo (async), pydantic-settings, pytest, LangSmith (tracing, sandbox, evals, deployment). Repo démo : FastAPI + pytest + GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-05-shipwright-design.md` (lire en entier, en particulier §4.3 scénarios et §4.8 décisions D1–D8 + D4b).

## Global Constraints

- Langue de tout ce que l'agent produit ou lit (prompts, skills, PR, CHANGELOG, commentaires Jira, tickets, rubriques d'éval, README) : **anglais** (D7).
- SDK open source `create_deep_agent` — pas Managed Deep Agents.
- `FilesystemBackend` / `LocalShellBackend` : **jamais** dans le graphe déployé ; `LocalShellBackend` autorisé uniquement en mode `SHIPWRIGHT_SANDBOX_PROVIDER=local` (dev/tests/évals), avec un **environnement shell nettoyé** (aucune variable de `Settings` visible par `execute`).
- Aucun chemin de repo codé en dur dans les commandes shell ou les prompts : toujours `Workspace.shell_repo_dir` / placeholder `{repo_dir}` (le chemin réel diffère entre sandbox LangSmith et provider local sous Windows).
- `backend=` reçoit une instance, jamais une `lambda` ; namespaces via `namespace=lambda rt: ...`.
- Aucun outil MCP d'**écriture** brut exposé à un modèle ; seuls les outils composites de la Task 10.
- La sandbox ne reçoit **aucun** credential GitHub en écriture ; le repo démo est **public** et cloné en HTTPS anonyme.
- Toute écriture GitHub/Jira passe par `interrupt_on` (D1) ; `publish_pull_request` exige une approbation du review gate portant sur le diff exact publié.
- Limites (valeurs par défaut, configurables) : `developer` max **3** tentatives de tests (scénario 5) ; reviewer ↔ developer max **2** cycles ; `ModelCallLimitMiddleware(run_limit=150)` sur l'orchestrateur ; TTL sandbox `idle_ttl_seconds=3600`.
- Versions : vérifier à l'install la dernière version de `deepagents` et exiger `>=0.7.3` (permissions `interrupt` ≥ 0.6.8, override de middleware ≥ 0.7, delete exact ≥ 0.7.3). Toute API Deep Agents utilisée doit être vérifiée dans la doc live (docs.langchain.com) au moment de l'implémentation ; en cas d'écart avec ce plan, suivre la doc et noter l'écart dans le commit.
- Variables LangSmith : `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=shipwright`.
- Les tests unitaires ne font **aucun** appel réseau ; les tests qui appellent OpenAI/MCP réels sont marqués `@pytest.mark.llm` ou `@pytest.mark.live` et exclus par défaut.
- Actions sortantes réelles (création du repo GitHub, du projet Jira, push, déploiement) : **demander confirmation à l'utilisateur** avant de les exécuter.

## Review Focus

1. **Ticket inexistant ou clé mal formée** (`SHIP-999`, `ship 42`) → l'agent répond clairement « ticket introuvable », sans stacktrace ni action. *(test : Task 5 `test_get_issue_not_found_raises_ticket_not_found`, Task 10 `test_read_ticket_tool_reports_missing_ticket`)*
2. **Diff vide** (le developer n'a rien changé, ou a tout annulé) → le review gate et `publish_pull_request` refusent avec un message explicite. *(Task 10 `test_review_gate_refuses_empty_diff`)*
3. **Publication sans approbation, ou diff modifié après approbation** → `publish_pull_request` refuse. *(Task 10 `test_publish_refuses_without_approval`, `test_publish_refuses_when_diff_changed_after_approval`)*
4. **Reprise d'un interrupt après expiration de la sandbox** (HITL laissé ouvert plus que le TTL) → le workspace est recréé, l'agent détecte que les changements approuvés ont disparu et le dit au lieu de publier une PR vide. *(Task 7 `test_ensure_workspace_recreates_missing_sandbox`, Task 10 `test_publish_refuses_when_diff_changed_after_approval` couvre le diff vide post-reprise)*
5. **Serveur MCP injoignable ou mauvais token** au démarrage → erreur claire nommant le serveur et la variable d'env à vérifier. *(Task 5 `test_load_mcp_tools_wraps_connection_error`)*
6. **Ticket retraité** (répétition de démo, branche `shipwright/<key>` ou PR déjà existante) → la branche est réutilisée et la PR ouverte est mise à jour, au lieu d'un échec au moment « wow ». *(Task 10 `test_publish_reuses_existing_branch_and_pr`)*

---

## File Structure

```
Project/                                   # repo git "shipwright"
├── pyproject.toml · uv.lock · langgraph.json · .env.example · .gitignore · README.md
├── src/shipwright/
│   ├── __init__.py
│   ├── config.py              # Settings (pydantic-settings)
│   ├── context.py             # ShipwrightContext (context_schema) + ShipwrightState (state_schema)
│   ├── tickets.py             # Ticket model + ticket key parsing
│   ├── integrations/
│   │   ├── mcp_client.py      # MultiServerMCPClient config + load_mcp_tools()
│   │   ├── jira.py            # JiraGateway Protocol + McpJiraGateway
│   │   └── github.py          # GitHubGateway Protocol + McpGitHubGateway
│   ├── knowledge/
│   │   ├── ingest.py          # LlamaParse → chunks → Pinecone
│   │   └── search.py          # KnowledgeSearcher (Pinecone)
│   ├── tools/
│   │   ├── research.py        # knowledge_search, web_search tools
│   │   ├── jira_tools.py      # read_jira_ticket, update_jira_ticket, create_jira_subtasks
│   │   ├── review_gate.py     # request_human_review (interrupt)
│   │   └── publish.py         # publish_pull_request
│   ├── workspace.py           # Workspace: sandbox provider, clone, diff, changed files, tests
│   ├── backends.py            # build_backend() CompositeBackend
│   ├── permissions.py         # PERMISSIONS
│   ├── policies.py            # seed_policies()
│   ├── middleware/
│   │   ├── tool_filter.py     # ExcludeToolsMiddleware
│   │   ├── audit.py           # AuditMiddleware (Mongo)
│   │   └── stack.py           # build_orchestrator_middleware()
│   ├── subagents/
│   │   ├── prompts/*.md       # researcher.md, code_analyst.md, developer.md, reviewer.md
│   │   └── specs.py           # build_subagents()
│   ├── prompts/orchestrator.md
│   ├── skills/<name>/SKILL.md # coding-conventions, pr-template, jira-workflow, testing-guidelines
│   ├── policies/POLICIES.md   # source des policies org
│   ├── agent.py               # build_agent()
│   ├── graph.py               # make_graph(config) — entrée langgraph.json
│   ├── persistence.py         # Mongo checkpointer/store pour le mode local
│   └── cli.py                 # `shipwright run SHIP-42` avec HITL terminal
├── knowledge/                 # specs & ADR (MD + 1 PDF) de taskboard-api
├── demo/
│   ├── tickets.yaml           # tickets démo (source pour seed Jira + fakes)
│   └── taskboard-api/         # repo démo (git séparé, ignoré par ce repo)
├── scripts/  seed_jira.py · ingest_knowledge.py · seed_policies.py · build_knowledge_pdf.py · smoke_live.py
├── tests/    unit/… · fakes.py · conftest.py
└── evals/    dataset.py · harness.py · evaluators.py · run_evals.py
```

---

### Task 1: Scaffold du repo et configuration typée

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/shipwright/__init__.py`, `src/shipwright/config.py`, `tests/conftest.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings, préfixe d'env aucun, lit `.env`) et `get_settings() -> Settings` (cache `lru_cache`). Champs :
  `openai_api_key: SecretStr`, `model_strong: str = "openai:gpt-5.5"`, `model_light: str` (requis, pas de défaut — l'implémenteur vérifie le nom disponible côté OpenAI et le met dans `.env.example`), `langsmith_api_key: SecretStr`, `mongodb_uri: SecretStr`, `mongodb_db: str = "shipwright"`, `pinecone_api_key: SecretStr`, `pinecone_index: str = "shipwright-knowledge"`, `pinecone_namespace: str = "taskboard-api"`, `llama_cloud_api_key: SecretStr`, `tavily_api_key: SecretStr`, `github_pat: SecretStr`, `github_mcp_url: str = "https://api.githubcopilot.com/mcp/"`, `shipwright_repo: str` (format `owner/name`), `jira_url: str`, `jira_username: str`, `jira_api_token: SecretStr`, `jira_project: str = "SHIP"`, `shipwright_sandbox_provider: Literal["langsmith","local"] = "langsmith"`, `max_test_attempts: int = 3`, `max_review_cycles: int = 2`, `orchestrator_model_call_limit: int = 150`, `sandbox_idle_ttl_seconds: int = 3600`, `org_id: str = "default-org"`.

- [ ] **Step 1:** `git init` à la racine `Project/`, `uv init --package --name shipwright --python 3.12`, ajouter les dépendances listées dans Tech Stack + dev (`pytest`, `pytest-asyncio`, `ruff`, `langgraph-cli[inmem]`). Noter la version résolue de `deepagents` (doit être ≥ 0.7.3). `.gitignore` : `.env`, `.venv/`, `demo/taskboard-api/`, `__pycache__/`, `.langgraph_api/`. Configurer pytest : `asyncio_mode = "auto"`, markers `llm`, `live`, `addopts = "-m 'not llm and not live'"`.
- [ ] **Step 2: Write the failing test**

```python
def test_settings_reads_required_fields(monkeypatch):
    for k, v in REQUIRED_ENV.items():  # dict défini dans le test, une valeur factice par champ requis
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.jira_project == "SHIP"
    assert s.shipwright_sandbox_provider == "langsmith"
    assert s.max_test_attempts == 3 and s.max_review_cycles == 2
    assert s.github_pat.get_secret_value() == REQUIRED_ENV["GITHUB_PAT"]

def test_settings_rejects_bad_repo_format(monkeypatch):
    ...  # SHIPWRIGHT_REPO="noslash" → ValidationError

def test_secrets_not_in_repr(monkeypatch):
    ...  # repr(Settings) ne contient pas la valeur de GITHUB_PAT
```

- [ ] **Step 3:** Run `uv run pytest tests/unit/test_config.py -v` → FAIL (module absent).
- [ ] **Step 4:** Implémenter `Settings` / `get_settings()` dans `src/shipwright/config.py` ; validateur `shipwright_repo` (regex `^[\w.-]+/[\w.-]+$`). `.env.example` liste toutes les variables (valeurs vides) + `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=shipwright`.
- [ ] **Step 5:** Run → PASS. `uv run ruff check .` → clean.
- [ ] **Step 6: Commit** `chore: scaffold shipwright package and typed settings`

---

### Task 2: Repo démo `taskboard-api`

**Files:** (dans `demo/taskboard-api/`, repo git séparé)
- Create: `pyproject.toml`, `uv.lock` (commité), `.gitignore` (`.venv/`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, `.coverage`), `app/main.py`, `app/routers/{projects,tasks,health}.py`, `app/models.py`, `app/services/{task_service,project_service}.py`, `app/storage.py` (stockage in-memory), `tests/test_*.py`, `.github/workflows/ci.yml`, `README.md`, `CHANGELOG.md`

**Interfaces:**
- Produces: un service FastAPI public sur GitHub `<owner>/taskboard-api` (valeur de `SHIPWRIGHT_REPO`), commande de test `uv run pytest -q` (ou `pip install -e . && pytest -q` — choisir **une** commande et la documenter dans `README.md` ; elle sera réutilisée par `Workspace.run_tests` et le skill `testing-guidelines`).
- Contenu minimal (assez pour que l'analyse ait du sens) : CRUD projets, CRUD tâches rattachées à un projet (statut `todo|in_progress|done`, assignee email), pagination simple, format d'erreur uniforme (ADR-001, Task 3). **Délibérément absents** (ce seront les tickets démo) : export CSV, endpoint `/health` détaillé, rate limiting, validation email vide (bug volontaire : `POST /tasks` avec `assignee=""` renvoie 500).

- [ ] **Step 1:** Écrire les tests du service (≥ 15 tests couvrant CRUD et pagination) et un test `test_create_task_with_empty_assignee_returns_500` marqué `xfail(strict=True, reason="SHIP bug demo")` documentant le bug volontaire.
- [ ] **Step 2:** Run → FAIL (app absente).
- [ ] **Step 3:** Implémenter l'app.
- [ ] **Step 4:** Run tests → PASS (le xfail est attendu). CI : `ci.yml` lance la même commande sur push/PR.
- [ ] **Step 5:** Commit dans `demo/taskboard-api` : `feat: initial taskboard-api service`.
- [ ] **Step 6:** **Demander confirmation à l'utilisateur**, puis créer le repo GitHub **public** `taskboard-api` sur son compte et pousser `main`. Activer la protection de branche `main` (PR obligatoire). Mettre `SHIPWRIGHT_REPO` dans `.env`.

---

### Task 3: Base de connaissances et ingestion LlamaParse → Pinecone

**Files:**
- Create: `knowledge/specs/EXPORT-CSV-v2.md`, `knowledge/specs/HEALTH-v1.md`, `knowledge/adr/ADR-001-error-format.md`, `knowledge/adr/ADR-002-rate-limiting.md`, `knowledge/architecture/overview.md`, `scripts/build_knowledge_pdf.py` (produit `knowledge/specs/EXPORT-CSV-v2.pdf` depuis le MD via `fpdf2`), `src/shipwright/knowledge/ingest.py`, `src/shipwright/knowledge/search.py`, `scripts/ingest_knowledge.py`
- Test: `tests/unit/test_ingest.py`, `tests/unit/test_search.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class KnowledgeChunk: text: str; source: str; doc_id: str; section: str`
  - `def chunk_markdown(markdown: str, *, source: str, doc_id: str) -> list[KnowledgeChunk]` — découpe par titres Markdown (`MarkdownHeaderTextSplitter` h1–h3) puis `RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150)` ; `section` = chemin de titres joint par `" > "`.
  - `async def parse_document(path: Path, settings: Settings) -> str` — `.md` lu tel quel ; `.pdf` via `LlamaParse(result_type="markdown")` (`llama_cloud_services`).
  - `async def ingest_directory(root: Path, settings: Settings, *, vector_store: VectorStore) -> int` — retourne le nombre de chunks ; ids déterministes `f"{doc_id}:{i}"` (ré-ingestion idempotente) ; `doc_id` = nom de fichier sans extension (ex. `EXPORT-CSV-v2`).
  - `class KnowledgeSearcher: def __init__(self, vector_store: VectorStore); async def search(self, query: str, k: int = 5) -> list[KnowledgeChunk]`
  - `def build_vector_store(settings: Settings) -> PineconeVectorStore` — embeddings `OpenAIEmbeddings(model="text-embedding-3-small")`, index `settings.pinecone_index`, namespace `settings.pinecone_namespace`. `scripts/ingest_knowledge.py` crée l'index s'il n'existe pas (dimension 1536, cosine, serverless).
- Contenu : les specs portent des **critères d'acceptation numérotés** (`AC-1`, `AC-2`…) que les tickets démo référencent ; `EXPORT-CSV-v2` : `GET /projects/{id}/tasks/export.csv`, colonnes exactes `id,title,status,assignee,created_at`, en-tête présent, `Content-Type: text/csv`, 404 si projet absent selon ADR-001.

- [ ] **Step 1: Write the failing tests**

```python
def test_chunk_markdown_keeps_section_path():
    md = "# Export CSV\n## Acceptance criteria\nAC-1 header row present\n"
    chunks = chunk_markdown(md, source="knowledge/specs/EXPORT-CSV-v2.md", doc_id="EXPORT-CSV-v2")
    assert chunks[-1].section == "Export CSV > Acceptance criteria"
    assert "AC-1" in chunks[-1].text

async def test_ingest_directory_uses_deterministic_ids(tmp_path):
    (tmp_path / "ADR-001-error-format.md").write_text("# ADR-001\nUse problem+json\n")
    store = FakeVectorStore()          # tests/fakes.py, enregistre add_texts(ids=…)
    n = await ingest_directory(tmp_path, settings_stub(), vector_store=store)
    assert n == 1 and store.ids == ["ADR-001-error-format:0"]

async def test_search_maps_metadata_to_chunks():
    store = FakeVectorStore.with_docs([("AC-1 header", {"source": "s.md", "doc_id": "X", "section": "A"})])
    [c] = await KnowledgeSearcher(store).search("header", k=1)
    assert (c.doc_id, c.section) == ("X", "A")
```

- [ ] **Step 2:** Run `uv run pytest tests/unit/test_ingest.py tests/unit/test_search.py -v` → FAIL.
- [ ] **Step 3:** Implémenter `ingest.py`, `search.py`, `FakeVectorStore` dans `tests/fakes.py`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5 (live):** `uv run python scripts/build_knowledge_pdf.py && uv run python scripts/ingest_knowledge.py` → affiche un nombre de chunks > 0 ; une recherche « CSV export columns » renvoie un chunk de `EXPORT-CSV-v2` (vérifier que le PDF est bien passé par LlamaParse dans les logs).
- [ ] **Step 6: Commit** `feat: knowledge base and LlamaParse→Pinecone ingestion`

---

### Task 4: Outils de recherche (researcher)

**Files:**
- Create: `src/shipwright/tools/research.py`
- Test: `tests/unit/test_research_tools.py`

**Interfaces:**
- Consumes: `KnowledgeSearcher` (Task 3).
- Produces:
  - `def make_knowledge_search_tool(searcher: KnowledgeSearcher) -> BaseTool` — outil `knowledge_search(query: str, k: int = 5) -> str` ; sortie texte : un bloc par chunk `[{doc_id} § {section}] ({source})\n{text}` ; si aucun résultat : `"No internal documents matched."`
  - `def make_web_search_tool(settings: Settings) -> BaseTool` — `TavilySearch(max_results=5)` de `langchain_tavily`, nom d'outil `web_search`.

- [ ] **Step 1: Failing tests** : `test_knowledge_search_formats_citations` (fake searcher → sortie contient `[EXPORT-CSV-v2 § Acceptance criteria]`), `test_knowledge_search_empty` (→ `"No internal documents matched."`), `test_web_search_tool_name` (`tool.name == "web_search"`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implémenter. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: research tools (Pinecone knowledge search, Tavily web search)`

---

### Task 5: Intégrations MCP (Jira, GitHub) derrière des interfaces

**Files:**
- Create: `src/shipwright/tickets.py`, `src/shipwright/integrations/{mcp_client,jira,github}.py`, `tests/fakes.py` (ajouts)
- Test: `tests/unit/test_tickets.py`, `tests/unit/test_jira_gateway.py`, `tests/unit/test_github_gateway.py`, `tests/unit/test_mcp_client.py`

**Interfaces:**
- Produces:
  - `tickets.py` : `class TicketNotFound(Exception)`, `def parse_ticket_key(raw: str, project: str) -> str` (normalise `"ship-42"`, `" SHIP-42 "` → `"SHIP-42"` ; lève `ValueError` sinon) ; `@dataclass(frozen=True) class Ticket: key: str; summary: str; description: str; status: str; acceptance_criteria: list[str]; spec_refs: list[str]; issue_type: str` ; `def extract_acceptance_criteria(description: str) -> list[str]` (lignes de la section `Acceptance criteria` / items `- [ ]` ou `AC-n`) ; `def extract_spec_refs(description: str) -> list[str]` (motifs `[A-Z]+(-[A-Z0-9]+)*-v\d+` et `ADR-\d{3}`).
  - `jira.py` : `class JiraGateway(Protocol)` avec `async get_issue(key) -> Ticket`, `async add_comment(key, body: str) -> str` (id), `async transition(key, to_status: str) -> None`, `async create_subtask(parent_key, summary, description) -> str` (clé), `async create_issue(project, summary, description, issue_type) -> str` (clé). `class McpJiraGateway(JiraGateway)` : construit depuis `dict[str, BaseTool]` (outils MCP `mcp-atlassian`) ; mappe sur `jira_get_issue`, `jira_add_comment`, `jira_get_transitions` + `jira_transition_issue` (résolution du nom de statut → id de transition, `ValueError` si inconnu), `jira_create_issue` (type Sub-task pour `create_subtask`, type libre pour `create_issue`). Les noms exacts d'outils sont des constantes en tête de module ; **Step 0 les vérifie** contre `list_tools()`.
  - `github.py` : `@dataclass(frozen=True) class FileChange: path: str; content: str | None` (None = suppression) ; `class GitHubGateway(Protocol)` avec `async create_branch(repo, branch, from_branch="main") -> None`, `async push_files(repo, branch, files: list[FileChange], message: str) -> str` (sha), `async create_pull_request(repo, head, base, title, body, draft: bool=False) -> str` (URL), `async branch_exists(repo, branch) -> bool`, `async find_open_pull_request(repo, head) -> str | None` (URL), `async update_pull_request(repo, pr_url, title, body) -> None`. `class McpGitHubGateway` : mappe sur les outils du serveur GitHub MCP officiel (`create_branch`, `push_files`, `create_pull_request`, `list_branches`, `list_pull_requests`, `update_pull_request` ; suppression de fichiers : `delete_file`) — noms vérifiés en Step 0.
  - `mcp_client.py` : `def mcp_server_config(settings) -> dict` → `{"github": {"transport": "streamable_http", "url": settings.github_mcp_url, "headers": {"Authorization": f"Bearer {pat}"}}, "atlassian": {"transport": "stdio", "command": "mcp-atlassian", "args": [], "env": {"JIRA_URL":…, "JIRA_USERNAME":…, "JIRA_API_TOKEN":…}}}` ; `class McpConnectionError(RuntimeError)` ; `async def load_mcp_tools(settings) -> dict[str, dict[str, BaseTool]]` (par serveur, via `MultiServerMCPClient(...).get_tools(server_name=...)`) qui convertit toute exception de connexion en `McpConnectionError(f"Cannot reach MCP server '{name}'. Check {env_vars}.")`.
  - `tests/fakes.py` : `FakeJiraGateway(tickets: dict[str, Ticket])` (enregistre `comments`, `transitions`, `subtasks`), `FakeGitHubGateway(existing_branches=(), open_prs={})` (enregistre `branches`, `pushes`, `prs`, `pr_updates`, renvoie `https://github.com/fake/repo/pull/1`).

- [ ] **Step 0 (live, une fois) :** script jetable qui liste les noms d'outils des deux serveurs MCP ; reporter les noms réels dans les constantes ; si `mcp-atlassian` n'est pas installable comme dépendance, ou si le PAT ne fonctionne pas sur le GitHub MCP distant, s'arrêter et le signaler à l'utilisateur avant de continuer.
- [ ] **Step 1: Failing tests**

```python
def test_parse_ticket_key_normalizes():
    assert parse_ticket_key(" ship-42 ", "SHIP") == "SHIP-42"
    with pytest.raises(ValueError): parse_ticket_key("ship 42", "SHIP")
    with pytest.raises(ValueError): parse_ticket_key("ABC-1", "SHIP")

def test_extract_acceptance_criteria_and_refs():
    d = "See EXPORT-CSV-v2 and ADR-001.\n## Acceptance criteria\n- [ ] AC-1 header row\n- [ ] AC-2 404 on unknown project\n"
    assert extract_acceptance_criteria(d) == ["AC-1 header row", "AC-2 404 on unknown project"]
    assert extract_spec_refs(d) == ["EXPORT-CSV-v2", "ADR-001"]

async def test_get_issue_not_found_raises_ticket_not_found():
    gw = McpJiraGateway(tools={"jira_get_issue": FakeTool(raises=ToolException("Issue does not exist"))})
    with pytest.raises(TicketNotFound): await gw.get_issue("SHIP-999")

async def test_transition_resolves_status_name():
    ...  # fake jira_get_transitions → [{"id": "31", "name": "In Review"}] ; transition("SHIP-1","In Review") appelle jira_transition_issue avec transition_id="31"

async def test_push_files_passes_contents():
    ...  # FakeTool enregistre les args ; vérifier repo/branch/files/message

async def test_load_mcp_tools_wraps_connection_error(monkeypatch):
    ...  # MultiServerMCPClient.get_tools lève ConnectionError → McpConnectionError dont le message contient "github" et "GITHUB_PAT"
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implémenter (+ `FakeTool` dans `tests/fakes.py`). **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: Jira and GitHub gateways over MCP with fakes`

---

### Task 6: Tickets démo et seed Jira

**Files:**
- Create: `demo/tickets.yaml`, `scripts/seed_jira.py`, `src/shipwright/demo_tickets.py`
- Test: `tests/unit/test_demo_tickets.py`

**Interfaces:**
- Produces: `def load_demo_tickets(path: Path = DEFAULT) -> list[DemoTicket]` ; `DemoTicket(scenario: int, summary: str, description: str, issue_type: str, expected: Literal["pr","questions","subtasks","draft_or_stop"])`. Une entrée par scénario 1–7 (§4.3) : 1 bug assignee vide ; 2 export CSV (réf. `EXPORT-CSV-v2`, AC listés) ; 3 rate limiting (réf. `ADR-002`, demande de choix de lib) ; 4 « Improve performance » sans AC ; 5 ticket dont un AC est impossible à satisfaire dans les tests (ex. exiger une dépendance réseau externe indisponible en sandbox) ; 6 endpoint `/health` détaillé (`HEALTH-v1`) — utilisé avec un feedback humain scripté ; 7 « Rewrite the whole authentication system with OAuth2 and RBAC ».
- `scripts/seed_jira.py` : crée les tickets dans le projet `settings.jira_project` via `McpJiraGateway.create_issue` (Task 5) ; écrit le mapping `scenario → clé` dans `demo/seeded.json` ; idempotent (ne recrée pas un scénario déjà présent dans `seeded.json`).

- [ ] **Step 1: Failing test** : `test_demo_tickets_cover_all_scenarios` (scénarios == {1..7}) ; `test_scenario_4_has_no_acceptance_criteria` (`extract_acceptance_criteria(desc) == []`) ; `test_scenario_2_references_export_spec`.
- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5 (live) :** **Demander confirmation**, l'utilisateur crée le projet Jira `SHIP` (Scrum/Kanban, statuts `To Do / In Progress / In Review / Done`) ; lancer `uv run python scripts/seed_jira.py` → 7 tickets créés, `demo/seeded.json` écrit.
- [ ] **Step 6: Commit** `feat: demo tickets for all seven scenarios and Jira seeding`

---

### Task 7: Workspace sandbox (clone, diff, tests, skills)

**Files:**
- Create: `src/shipwright/workspace.py`, `src/shipwright/tools/workspace_tools.py`
- Test: `tests/unit/test_workspace.py`

**Interfaces:**
- Consumes: `Settings`, `FileChange` (Task 5).
- Produces:
  - Constantes (chemins **virtuels** des outils filesystem) : `REPO_DIR = "/workspace/repo"`, `SKILLS_DIR = "/skills"`.
  - `Workspace.shell_repo_dir: str` — chemin du repo **tel que vu par `execute`** (= `REPO_DIR` en sandbox LangSmith ; chemin hôte réel en provider local). Toutes les commandes shell l'utilisent ; les prompts reçoivent `{repo_dir}`.
  - Base de diff : `ensure_cloned()` enregistre le SHA de `HEAD` après clone dans un fichier **hors du repo** (`{shell_repo_dir}/../.shipwright/base_sha`) ; `async Workspace.base_sha() -> str` le relit (le Workspace est reconstruit à chaque run).
  - `class Workspace:` attributs `backend: SandboxBackendProtocol`, `repo: str` ;
    - `async def ensure_cloned(self) -> bool` — si `REPO_DIR/.git` absent : `git clone --depth 50 https://github.com/{repo}.git {REPO_DIR}` + `git config user.name/email` (Shipwright bot) ; retourne `True` si cloné maintenant. Puis upload des skills (`src/shipwright/skills/**`) vers `SKILLS_DIR` via `upload_files`.
    - `async def diff(self) -> str` — `git -C {shell_repo_dir} add -N . && git -C {shell_repo_dir} diff {base_sha}` (inclut fichiers nouveaux **et** survit à un `git commit` du developer).
    - `async def diff_hash(self) -> str` — sha256 hex du diff.
    - `async def changed_files(self) -> list[FileChange]` — `git diff --name-status -z {base_sha}` + `git ls-files --others --exclude-standard -z` (fichiers individuels, ignorés exclus, jamais de répertoires) → contenus via `download_files` ; suppressions → `content=None`.
    - `async def run_tests(self) -> TestReport` — `@dataclass TestReport: passed: bool; summary: str; output_tail: str` (exit code du runner de Task 2 ; `output_tail` = 80 dernières lignes).
  - `async def open_workspace(settings, *, thread_id: str, repo: str) -> Workspace` — provider `langsmith` : `SandboxClient` ; réutilise la sandbox nommée `f"shipwright-{thread_id}"` sinon la crée (`idle_ttl_seconds=settings.sandbox_idle_ttl_seconds`) ; `LangSmithSandbox(sandbox=...)`. Provider `local` : `LocalShellBackend` sur un dossier `tmp/shipwright/{thread_id}` avec env nettoyé (seulement `PATH`, `HOME`/`USERPROFILE`, `SYSTEMROOT`, `TEMP`) ; `shell_repo_dir` = chemin hôte réel du repo.
  - `def make_diff_tool(workspace: Workspace) -> BaseTool` (dans `tools/workspace_tools.py`) — outil lecture seule `show_workspace_diff() -> str` : renvoie `await workspace.diff()` ou `"No changes."` ; tronqué à 20 000 caractères avec la mention `[diff truncated]`. Utilisé par le reviewer et l'orchestrateur.
  - Exécution des commandes : `backend.execute(cmd)` ; parser la ligne finale `[Command ... exit code N]` via `def parse_exit_code(output: str) -> int`.

- [ ] **Step 0 (vérification, cette machine Windows) :** script jetable : créer un `LocalShellBackend` (lire sa signature dans la doc / le code installé), exécuter `pwd`, `git --version`, écrire un fichier via `write_file("/workspace/x.txt")` et le relire via `execute` ; consigner (a) le shell utilisé, (b) comment `execute` traite les chemins virtuels absolus, (c) si un `env` custom est accepté. Si le provider local ne peut pas fonctionner proprement sous Windows (shell, chemins ou env non maîtrisables), le signaler et basculer les tests `llm` et les évals sur le provider `langsmith`.
- [ ] **Step 1: Failing tests** (avec `FakeSandbox` dans `tests/fakes.py` : dict de fichiers + table `commande → sortie`) :

```python
def test_parse_exit_code():
    assert parse_exit_code("4\n[Command succeeded with exit code 0]") == 0
    assert parse_exit_code("boom\n[Command failed with exit code 2]") == 2

async def test_ensure_cloned_clones_once_and_uploads_skills(): ...
    # 1er appel → True, commande git clone exécutée, /skills/pr-template/SKILL.md présent ; 2e appel → False

async def test_changed_files_handles_added_modified_deleted(): ...
    # name-status M app/a.py, D app/c.py + untracked app/b.py → 3 FileChange, c.py content None

async def test_changed_files_never_includes_ignored_or_cache_paths(): ...
    # .pytest_cache/, __pycache__/x.pyc, .venv/ présents mais ignorés → absents du résultat

async def test_diff_survives_developer_commit(): ...
    # base_sha enregistré, puis un commit local → diff() non vide, contient le changement

async def test_run_tests_reports_failure_tail(): ...

async def test_ensure_workspace_recreates_missing_sandbox(): ...
    # FakeSandboxClient sans sandbox du nom attendu → create_sandbox appelé avec name="shipwright-t1"

async def test_diff_tool_reports_no_changes_and_truncates(): ...
```

- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5 (live, provider langsmith) :** test `@pytest.mark.live` `test_live_clone_and_run_tests` : clone de `SHIPWRIGHT_REPO`, `run_tests().passed is True`, puis suppression de la sandbox. Si les sandboxes LangSmith ne sont pas disponibles sur le plan de l'utilisateur, **s'arrêter et le signaler** (fallback Daytona/Modal/Runloop = décision utilisateur).
- [ ] **Step 6: Commit** `feat: sandbox workspace with clone, diff, tests and skills upload`

---

### Task 8: Contexte, état, backends, permissions, policies

**Files:**
- Create: `src/shipwright/context.py`, `src/shipwright/backends.py`, `src/shipwright/permissions.py`, `src/shipwright/policies.py`, `src/shipwright/policies/POLICIES.md`, `scripts/seed_policies.py`
- Test: `tests/unit/test_backends.py`, `tests/unit/test_permissions.py`

**Interfaces:**
- Produces:
  - `@dataclass class ShipwrightContext: user_id: str; jira_project: str = "SHIP"`
  - `class ShipwrightState(AgentState)` (vérifier le type de base d'état attendu par `create_deep_agent(state_schema=...)`) avec champ `approved_diff_hash: NotRequired[str | None]`.
  - `def build_backend(workspace: Workspace, settings: Settings) -> CompositeBackend` → `CompositeBackend(default=workspace.backend, routes={"/memories/": StoreBackend(namespace=lambda rt: ("users", rt.context.user_id)), "/policies/": StoreBackend(namespace=lambda rt: ("org", settings.org_id))})`.
  - `PERMISSIONS: list[FilesystemPermission]` = `[FilesystemPermission(operations=["write"], paths=["/policies/**"], mode="deny")]` (la mémoire `/memories/**` reste en écriture pour l'orchestrateur).
  - `SUBAGENT_PERMISSIONS: list[FilesystemPermission]` = deny write sur `/policies/**` **et** `/memories/**` (les sous-agents ne modifient pas la mémoire).
  - `POLICIES.md` (anglais) : fichiers interdits (`.github/workflows/**`, `pyproject.toml` dépendances sans justification), max 400 lignes de diff par PR sinon proposer un découpage (scénario 7), jamais de secrets dans le code, toujours des tests pour un changement de comportement, format d'erreur ADR-001.
  - `async def seed_policies(store: BaseStore, settings) -> None` — écrit `/POLICIES.md` dans le namespace `("org", org_id)` avec `create_file_data` (clé selon la convention `StoreBackend` — vérifier dans la doc si la clé inclut le préfixe de route).

- [ ] **Step 1: Failing tests** (intégration réelle `create_deep_agent` sans LLM n'est pas nécessaire ; tester les backends directement avec `InMemoryStore` et un `Runtime` factice portant `context`) :

```python
def test_memories_route_is_user_scoped(): ...
    # écrire /memories/preferences.md pour user "alice", lire pour "bob" → absent

def test_policies_route_is_org_scoped_and_seeded(): ...
    # seed_policies puis read("/policies/POLICIES.md") contient "Forbidden paths"

def test_permissions_deny_policy_writes():
    assert PERMISSIONS[0].mode == "deny" and "/policies/**" in PERMISSIONS[0].paths

def test_subagent_permissions_deny_memory_writes(): ...
```

  Plus un test `@pytest.mark.llm` : agent minimal avec ce backend et `PERMISSIONS` à qui on demande d'écrire `/policies/POLICIES.md` → le fichier est inchangé.
- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5: Commit** `feat: composite backend with user memory, org policies and permissions`

---

### Task 9: Sous-agents et middleware de filtrage d'outils

**Files:**
- Create: `src/shipwright/middleware/tool_filter.py`, `src/shipwright/subagents/specs.py`, `src/shipwright/subagents/prompts/{researcher,code_analyst,developer,reviewer}.md`
- Test: `tests/unit/test_tool_filter.py`, `tests/unit/test_subagent_specs.py`

**Interfaces:**
- Consumes: outils de la Task 4, `SUBAGENT_PERMISSIONS` (Task 8), `Settings`.
- Produces:
  - `class ExcludeToolsMiddleware(AgentMiddleware)` — `__init__(self, excluded: frozenset[str])` ; `wrap_model_call` / `awrap_model_call` retirent de `request.tools` tout outil dont le nom est dans `excluded` (vérifier l'API `ModelRequest.override(tools=...)` dans la doc middleware LangChain).
  - `READ_ONLY_EXCLUDED = frozenset({"write_file", "edit_file", "delete", "execute"})`
  - `class ReviewReport(BaseModel)` : `verdict: Literal["pass","changes_requested"]`, `criteria: list[CriterionCheck]` (`id: str`, `met: bool`, `evidence: str`), `risks: list[str]`, `suggestions: list[str]`.
  - `def build_subagents(settings, *, knowledge_tool: BaseTool, web_tool: BaseTool, diff_tool: BaseTool) -> list[dict]` → 4 specs :

| name | model | tools | middleware | skills | autres |
|---|---|---|---|---|---|
| `researcher` | `model_light` | `[knowledge_tool, web_tool]` | `[ExcludeToolsMiddleware(READ_ONLY_EXCLUDED)]` | — | permissions `SUBAGENT_PERMISSIONS` |
| `code_analyst` | `model_light` | `[]` (outils filesystem intégrés) | `[ExcludeToolsMiddleware(READ_ONLY_EXCLUDED)]` | `["/skills/"]` | idem |
| `developer` | `model_strong` | `[]` (filesystem + `execute`) | — | `["/skills/"]` | idem |
| `reviewer` | `model_light` | `[diff_tool]` | `[ExcludeToolsMiddleware(READ_ONLY_EXCLUDED)]` | `["/skills/"]` | `response_format=ReviewReport`, idem |

  - Prompts (anglais) — points obligatoires : **researcher** cite chaque fait `[doc_id § section]` ou URL, n'invente pas de spec absente ; **code_analyst** travaille sous `{repo_dir}`, renvoie fichiers concernés + approche + tests existants, ne propose pas de code complet ; **developer** implémente dans `{repo_dir}`, lit `/policies/POLICIES.md`, lance les tests avec la commande du skill `testing-guidelines`, **au plus `{max_test_attempts}` tentatives** puis rapporte `STATUS: TESTS_FAILING` avec la dernière sortie, sinon `STATUS: TESTS_PASSING` ; n'exécute jamais `git commit/push` ; **reviewer** vérifie chaque critère d'acceptation reçu contre le diff (il obtient le diff via `show_workspace_diff`, sans `execute`), les policies et le skill `coding-conventions`.
  - Le `description` de chaque spec dit précisément **quand** l'utiliser (ex. researcher : « only when the ticket references a spec/ADR or needs external library knowledge »).

- [ ] **Step 1: Failing tests**

```python
async def test_exclude_tools_middleware_removes_write_tools():
    req = fake_model_request(tools=["read_file", "write_file", "execute", "grep"])
    seen = await run_awrap(ExcludeToolsMiddleware(READ_ONLY_EXCLUDED), req)
    assert [t.name for t in seen.tools] == ["read_file", "grep"]

def test_subagent_specs_shape():
    specs = {s["name"]: s for s in build_subagents(settings_stub(), knowledge_tool=t1, web_tool=t2, diff_tool=t3)}
    assert set(specs) == {"researcher", "code_analyst", "developer", "reviewer"}
    assert specs["reviewer"]["response_format"] is ReviewReport
    assert specs["developer"]["model"] == settings_stub().model_strong
    assert not any(isinstance(m, ExcludeToolsMiddleware) for m in specs["developer"].get("middleware", []))
    assert "3" in specs["developer"]["system_prompt"]   # max_test_attempts injecté
    assert all(s["permissions"] == SUBAGENT_PERMISSIONS for s in specs.values())
```

- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5: Commit** `feat: four scoped subagents with read-only tool filtering`

---

### Task 10: Outils HITL — lecture ticket, review gate, publication, Jira

**Files:**
- Create: `src/shipwright/tools/jira_tools.py`, `src/shipwright/tools/review_gate.py`, `src/shipwright/tools/publish.py`
- Test: `tests/unit/test_jira_tools.py`, `tests/unit/test_review_gate.py`, `tests/unit/test_publish.py`

**Interfaces:**
- Consumes: `JiraGateway`, `GitHubGateway`, `Ticket`, `parse_ticket_key`, `Workspace`, `ShipwrightState.approved_diff_hash`, `ReviewReport`.
- Produces (fabriques fermant sur les dépendances ; outils async) :
  - `make_jira_tools(jira: JiraGateway, settings) -> list[BaseTool]` :
    - `read_jira_ticket(key: str) -> str` — **lecture, pas d'interrupt** ; rend summary, status, description, `Acceptance criteria` (liste ou `NONE FOUND`), `Spec references` ; `TicketNotFound`/`ValueError` → texte `"Ticket {key} not found in project {project}."` (pas d'exception).
    - `update_jira_ticket(key: str, comment: str, transition_to: str | None = None) -> str`
    - `create_jira_subtasks(parent_key: str, subtasks: list[SubtaskDraft]) -> str` (`SubtaskDraft(summary, description)`) → liste des clés créées.
  - `make_review_gate_tool(workspace: Workspace) -> BaseTool` — `request_human_review(summary: str, test_report: str, review_report: str, tool_call_id: InjectedToolCallId) -> Command` :
    1. `diff = await workspace.diff()` ; si vide → retourne `ToolMessage("Nothing to review: the working tree has no changes.")` sans interrupt.
    2. `decision = interrupt({"kind": "review_gate", "summary":…, "diff": diff, "test_report":…, "review_report":…, "allowed": ["approve","feedback","reject"]})`.
    3. `approve` → `Command(update={"approved_diff_hash": sha256(diff), "messages":[ToolMessage("APPROVED. You may now call publish_pull_request.")]})` ; `feedback` → `approved_diff_hash: None` + message `"CHANGES REQUESTED by human: {message}. Send this feedback to the developer, then request review again. If the feedback is a reusable preference, record it in /memories/preferences.md."` ; `reject` → `approved_diff_hash: None` + `"REJECTED by human: {message}. Stop working on this ticket and report."` ; valeur invalide → `ValueError`.
  - `make_publish_tool(workspace, github: GitHubGateway, settings) -> BaseTool` — `publish_pull_request(ticket_key: str, title: str, body: str, changelog_entry: str, draft: bool = False, state: InjectedState) -> str` :
    - refuse (texte d'erreur, pas d'exception) si `state.get("approved_diff_hash")` absent ou ≠ `await workspace.diff_hash()` ;
    - branche `f"shipwright/{ticket_key.lower()}"` : si elle existe déjà, la réutiliser (push par-dessus) ; si une PR ouverte existe pour cette branche, la **mettre à jour** (titre/body) et renvoyer son URL au lieu d'en créer une ; fichiers = `await workspace.changed_files()` + `CHANGELOG.md` mis à jour (entrée insérée sous `## [Unreleased]`, créée si absente) ; PR `base="main"`, titre préfixé `f"[{ticket_key}] "` ; body doit contenir `ticket_key` (sinon ajouté en tête) ; retourne l'URL.
  - `HITL_INTERRUPTS: dict` = `{"publish_pull_request": {"allowed_decisions": ["approve","reject"]}, "update_jira_ticket": {"allowed_decisions": ["approve","edit","reject"]}, "create_jira_subtasks": {"allowed_decisions": ["approve","edit","reject"]}}`

- [ ] **Step 1: Failing tests** (fakes Jira/GitHub/Workspace ; pour `interrupt`, exécuter l'outil dans un petit `StateGraph` à un nœud avec `InMemorySaver`, puis reprendre avec `Command(resume=...)`) :

```python
async def test_read_ticket_tool_reports_missing_ticket(): ...   # → "Ticket SHIP-999 not found in project SHIP."
async def test_read_ticket_tool_flags_missing_criteria(): ...    # scénario 4 → "Acceptance criteria: NONE FOUND"
async def test_review_gate_refuses_empty_diff(): ...             # aucun interrupt levé
async def test_review_gate_approve_sets_hash(): ...              # state.approved_diff_hash == sha256(diff)
async def test_review_gate_feedback_clears_hash_and_relays_message(): ...
async def test_publish_refuses_without_approval(): ...           # FakeGitHub.prs == []
async def test_publish_refuses_when_diff_changed_after_approval(): ...
async def test_publish_creates_branch_files_changelog_and_pr():
    ...
    assert gh.branches == [("owner/taskboard-api", "shipwright/ship-2", "main")]
    assert "CHANGELOG.md" in [f.path for f in gh.pushes[0].files]
    assert gh.prs[0].title.startswith("[SHIP-2] ") and "SHIP-2" in gh.prs[0].body
async def test_publish_reuses_existing_branch_and_pr(): ...
    # FakeGitHub(existing_branches={"shipwright/ship-2"}, open_prs={"shipwright/ship-2": URL}) → pas de create_branch, pr_updates == [URL], retourne URL
async def test_update_jira_ticket_comment_and_transition(): ...
```

- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5: Commit** `feat: HITL review gate, guarded PR publishing and Jira tools`

---

### Task 11: Skills

**Files:**
- Create: `src/shipwright/skills/{coding-conventions,pr-template,jira-workflow,testing-guidelines}/SKILL.md`
- Test: `tests/unit/test_skills.py`

**Interfaces:**
- Produces: 4 skills (anglais), frontmatter `name` + `description` spécifique :
  - `coding-conventions` — style FastAPI du repo démo (routers/services/models, Pydantic, erreurs ADR-001, type hints, pas de logique dans les routers).
  - `testing-guidelines` — commande de test exacte (Task 2), un test par critère d'acceptation nommé `test_ac<n>_...`, tests de non-régression pour un bug.
  - `pr-template` — gabarit exact du body : `## Summary`, `## Linked ticket` (clé + lien Jira `{jira_url}/browse/{key}`), `## Acceptance criteria` (cases cochées avec preuve), `## Tests` (résumé), `## Self-review` (verdict + risques du reviewer), `## Notes`.
  - `jira-workflow` — quand commenter, gabarit du commentaire de clarification (scénario 4 : 2–4 questions numérotées, pas de code), gabarit du commentaire post-PR (lien PR + résumé), transition utilisée en v1 : uniquement `In Review` après la PR (pas de `In Progress`, pour rester à 3 interrupts), gabarit de découpage en sous-tickets (scénario 7).

- [ ] **Step 1: Failing test** : `test_skills_have_valid_frontmatter` — chaque `SKILL.md` parse en YAML front matter avec `name` == nom du dossier et `description` de ≥ 40 caractères ; `test_pr_template_sections` — le skill `pr-template` contient les 6 titres exacts ci-dessus.
- [ ] **Step 2–4:** FAIL → écrire les skills → PASS.
- [ ] **Step 5: Commit** `feat: engineering skills for conventions, tests, PRs and Jira workflow`

---

### Task 12: Middleware d'entreprise (PII, audit, limites, retries)

**Files:**
- Create: `src/shipwright/middleware/audit.py`, `src/shipwright/middleware/stack.py`
- Test: `tests/unit/test_audit.py`, `tests/unit/test_middleware_stack.py`

**Interfaces:**
- Produces:
  - `AUDITED_TOOLS = frozenset({"publish_pull_request", "update_jira_ticket", "create_jira_subtasks", "request_human_review"})`
  - `class AuditSink(Protocol): async def record(self, event: AuditEvent) -> None` ; `@dataclass AuditEvent: ts: datetime; thread_id: str; user_id: str; tool: str; args: dict; outcome: Literal["ok","error"]; result_preview: str` (≤ 500 caractères) ; `class MongoAuditSink(AuditSink)` (collection `audit_log`, `pymongo.AsyncMongoClient`) ; `class MemoryAuditSink` pour tests.
  - `class AuditMiddleware(AgentMiddleware)` — `awrap_tool_call` : pour un outil de `AUDITED_TOOLS`, appelle le handler puis enregistre l'événement (outcome `error` si exception, exception re-levée) ; ne doit jamais faire échouer l'appel si l'enregistrement échoue (log warning).
  - `def build_orchestrator_middleware(settings, audit_sink) -> list[AgentMiddleware]` dans cet ordre : `PIIMiddleware("email", strategy="redact", apply_to_input=True)`, `PIIMiddleware("github_token", detector=r"gh[pousr]_[A-Za-z0-9]{36,}", strategy="block", apply_to_input=True)` (vérifier la signature des détecteurs custom), `ModelCallLimitMiddleware(run_limit=settings.orchestrator_model_call_limit)`, `ModelRetryMiddleware` / `ToolRetryMiddleware` (noms à vérifier dans la doc LangChain middleware ; 2 retries, backoff exponentiel, uniquement sur erreurs réseau/rate limit), `ModelFallbackMiddleware(settings.model_light)`, `AuditMiddleware(audit_sink)`.
  - Note : le redaction email s'applique aux messages d'entrée ; les emails `assignee` du repo démo restent dans le code (pas des messages).

- [ ] **Step 1: Failing tests** : `test_audit_records_audited_tool_ok`, `test_audit_records_error_and_reraises`, `test_audit_ignores_unaudited_tools`, `test_audit_sink_failure_does_not_break_call`, `test_stack_order` (types dans l'ordre ci-dessus).
- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5: Commit** `feat: enterprise middleware stack with PII, limits, retries and Mongo audit`

---

### Task 13: Assemblage de l'agent, graphe, persistance, CLI

**Files:**
- Create: `src/shipwright/prompts/orchestrator.md`, `src/shipwright/agent.py`, `src/shipwright/graph.py`, `src/shipwright/persistence.py`, `src/shipwright/cli.py`, `langgraph.json`
- Test: `tests/unit/test_agent_build.py`, `tests/integration/test_scenario_bugfix.py` (`@pytest.mark.llm`)

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces:
  - `@dataclass class Dependencies: jira: JiraGateway; github: GitHubGateway; searcher: KnowledgeSearcher; web_tool: BaseTool; audit_sink: AuditSink`
  - `def build_agent(settings, deps: Dependencies, workspace: Workspace, *, checkpointer=None, store=None) -> CompiledStateGraph` — `create_deep_agent(name="shipwright", model=settings.model_strong, tools=[read_jira_ticket, update_jira_ticket, create_jira_subtasks, show_workspace_diff, request_human_review, publish_pull_request], system_prompt=<orchestrator.md formaté>, subagents=build_subagents(...), backend=build_backend(...), permissions=PERMISSIONS, skills=["/skills/"], memory=["/memories/preferences.md", "/policies/POLICIES.md"], interrupt_on=HITL_INTERRUPTS, middleware=build_orchestrator_middleware(...), context_schema=ShipwrightContext, state_schema=ShipwrightState, checkpointer=checkpointer, store=store)`. Vérifier que `memory=` tolère un fichier absent (premier run d'un utilisateur) ; sinon créer `preferences.md` vide à la volée.
  - `async def make_graph(config: RunnableConfig) -> CompiledStateGraph` (entrée `langgraph.json`) : `thread_id = config.get("configurable", {}).get("thread_id")` — si absent (le serveur peut appeler la fabrique sans thread, ex. chargement du schéma dans Studio), construire le graphe avec un workspace « schema-only » non cloné (aucune sandbox créée) ; vérifier ce comportement avec `langgraph dev`, `repo = config["configurable"].get("repo", settings.shipwright_repo)`, `workspace = await open_workspace(...)`, `await workspace.ensure_cloned()`, dépendances réelles (MCP via `load_mcp_tools`, Pinecone, Tavily, `MongoAuditSink`) ; **pas** de checkpointer/store (fournis par la plateforme).
  - `persistence.py` : `@asynccontextmanager async def mongo_persistence(settings) -> AsyncIterator[tuple[checkpointer, store]]` (`langgraph-checkpoint-mongodb` + `langgraph-store-mongodb`, base `settings.mongodb_db`).
  - `cli.py` : `shipwright run <TICKET> [--user USER] [--thread THREAD]` — mode local : `mongo_persistence`, `build_agent`, stream des mises à jour (todos, sous-agent actif, appels d'outils) dans le terminal ; à chaque `__interrupt__` : pour `review_gate`, afficher diff/tests/rapport et demander `approve/feedback/reject` (+ message) → `Command(resume={...})` ; pour `interrupt_on`, afficher les `action_requests` et demander une décision par action → `Command(resume={"decisions":[...]})`. Script d'entrée `[project.scripts] shipwright = "shipwright.cli:main"`.
  - `langgraph.json` : `{"dependencies": ["."], "graphs": {"shipwright": "./src/shipwright/graph.py:make_graph"}, "env": ".env", "checkpointer": {"backend": "mongo"}}` (+ `python_version: "3.12"`).
  - `orchestrator.md` (anglais) — doit encoder, dans cet ordre : lire `/policies/POLICIES.md` et `/memories/preferences.md` ; `read_jira_ticket` ; **si aucun critère d'acceptation ou ticket ambigu → `update_jira_ticket` avec 2–4 questions (skill `jira-workflow`) puis STOP** (scénario 4) ; **si le changement estimé dépasse les policies (taille/modules) → `create_jira_subtasks` puis STOP** (scénario 7) ; `write_todos` ; `researcher` seulement si refs de spec ou besoin externe, en parallèle de `code_analyst` ; `developer` ; si `TESTS_FAILING` après les tentatives → `request_human_review` en le disant explicitement, et si l'humain approuve → `publish_pull_request(draft=True)` (scénario 5) ; `reviewer` avec la liste des fichiers modifiés et les AC ; si `changes_requested` → retour au developer, **max `{max_review_cycles}` cycles** ; `request_human_review` ; sur feedback → developer puis nouvelle review (scénario 6) et enregistrer la préférence réutilisable dans `/memories/preferences.md` ; `publish_pull_request` (body selon `pr-template`) ; `update_jira_ticket` (commentaire avec lien PR, `transition_to="In Review"`) ; résumé final.

- [ ] **Step 1: Failing tests (unitaires, sans LLM)** : `test_build_agent_compiles_with_fakes` (le graphe compile avec fakes, `LocalShellBackend`/`FakeSandbox`, `InMemorySaver`, `InMemoryStore` ; l'appel `create_deep_agent` reçoit `skills=["/skills/"]`) ; `test_make_graph_without_thread_id_creates_no_sandbox` ; `test_orchestrator_tools_exclude_raw_mcp_writes` (aucun outil exposé ne s'appelle `push_files`, `create_pull_request`, `jira_add_comment`…) ; `test_langgraph_json_points_to_make_graph`.
- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5: Test d'intégration `@pytest.mark.llm`** `test_scenario_1_bugfix_reaches_review_gate` : fakes Jira (ticket scénario 1) / GitHub, provider `local` sur une copie du repo démo, OpenAI réel ; exécuter jusqu'au premier interrupt → `kind == "review_gate"`, `test_report` indique des tests passants, le diff touche `app/` et `tests/`. Puis `approve` → interrupt `publish_pull_request` → approve → interrupt `update_jira_ticket` → approve → `FakeGitHub.prs` a 1 PR, `FakeJira.comments["SHIP-1"]` contient l'URL. Run : `uv run pytest -m llm tests/integration/test_scenario_bugfix.py -v` → PASS.
- [ ] **Step 6 (live, local) :** `uv run langgraph dev` (vérifier que le serveur de dev accepte `"checkpointer": {"backend": "mongo"}` ; sinon le retirer pour le dev et ne le garder que pour le déploiement, en le documentant) → le graphe `shipwright` apparaît dans LangSmith Studio ; un run sur le ticket scénario 1 affiche todos, sous-agents et interrupts. Trace visible dans le projet LangSmith `shipwright`.
- [ ] **Step 7: Commit** `feat: assemble Shipwright orchestrator, graph factory, Mongo persistence and CLI`

---

### Task 14: Scénarios non nominaux (2–7) validés

**Files:**
- Modify: `src/shipwright/prompts/orchestrator.md`, prompts de sous-agents si nécessaire
- Test: `tests/integration/test_scenarios.py` (`@pytest.mark.llm`)

**Interfaces:**
- Consumes: `build_agent`, fakes, `load_demo_tickets`.
- Produces: `tests/integration/helpers.py` : `async def drive(agent, ticket_key, *, decide: Callable[[dict], Any], config) -> RunOutcome` — boucle invoke/resume tant qu'il y a des interrupts, `decide` reçoit la valeur d'interrupt et renvoie la valeur de resume ; `RunOutcome(interrupts: list[dict], subagents_called: list[str], tool_calls: list[str], final_message: str)` (extraits des messages : `task` → `subagent_type`). Réutilisé par la Task 15.

- [ ] **Step 1: Failing tests** (un par scénario, décisions scriptées) :
  - `test_s2_feature_uses_researcher_and_code_analyst` — `{"researcher","code_analyst","developer","reviewer"} <= set(subagents_called)` ; PR créée ; body contient `AC-1`.
  - `test_s3_external_knowledge_uses_web_search` — `web_search` appelé (via trajectoire du researcher, exposée par les messages du sous-agent ou la trace ; si non observable depuis le parent, vérifier via un `FakeWebTool` qui enregistre ses appels).
  - `test_s4_ambiguous_ticket_asks_and_stops` — aucun appel `request_human_review`/`publish_pull_request` ; 1 commentaire Jira contenant au moins 2 lignes `?`.
  - `test_s5_failing_tests_escalates` — interrupt review gate dont `test_report` signale l'échec ; décision `approve` → PR `draft=True`.
  - `test_s6_feedback_loop_and_memory` — 1er review gate → `feedback: "Use a dataclass for the health payload, not a dict"` → second review gate avec un diff différent ; `/memories/preferences.md` (store, user `alice`) contient `dataclass` ; puis **nouveau thread, même user**, ticket scénario 1 : le prompt système chargé contient la préférence (vérifier via le store / la mémoire chargée, pas via le comportement du LLM).
  - `test_s7_large_ticket_proposes_subtasks` — `create_jira_subtasks` appelé avec ≥ 2 sous-tickets, aucune PR.
- [ ] **Step 2:** Run `uv run pytest -m llm tests/integration/test_scenarios.py -v` → noter les échecs.
- [ ] **Step 3:** Ajuster prompts/descriptions de sous-agents (pas le code des tests) jusqu'à ce que chaque test passe **3 fois de suite**.
- [ ] **Step 4: Commit** `feat: robust handling of ambiguous, failing, oversized and revised tickets`

---

### Task 15: Évaluations offline LangSmith

**Files:**
- Create: `evals/dataset.py`, `evals/harness.py`, `evals/evaluators.py`, `evals/run_evals.py`
- Test: `tests/unit/test_evaluators.py`

**Interfaces:**
- Consumes: `drive()` (Task 14), `load_demo_tickets()`, fakes.
- Produces:
  - `dataset.py` : `def upsert_dataset(client: langsmith.Client, name: str = "shipwright-scenarios") -> str` — un exemple par ticket démo : inputs `{"ticket_key", "scenario"}`, outputs de référence `{"expected": DemoTicket.expected, "expected_subagents": [...], "acceptance_criteria": [...]}`.
  - `harness.py` : `async def run_example(inputs: dict) -> dict` — construit l'agent avec fakes + provider `local`, décisions scriptées par scénario (même table que Task 14), renvoie `{"outcome": "pr"|"questions"|"subtasks"|"stopped", "subagents": [...], "tests_passed": bool, "pr_body": str | None}`.
  - `evaluators.py` (fonctions pures + 1 LLM-judge) : `outcome_matches(run, example) -> {"key":"outcome","score":0|1}` ; `trajectory_subagents(run, example)` (score = rappel des sous-agents attendus, pénalité si `researcher` appelé au scénario 1) ; `no_pr_when_ambiguous` (scénario 4 → score 1 ssi `pr_body is None`) ; `tests_green` ; `criteria_covered` — LLM-judge (`model_light`) avec rubrique anglaise : chaque AC de référence est-il coché **avec preuve** dans `pr_body` (score = fraction).
  - `run_evals.py` : `uv run python evals/run_evals.py --repetitions 2` → `langsmith.aevaluate(run_example, data=..., evaluators=[...], experiment_prefix="shipwright")`.

- [ ] **Step 1: Failing tests** (évaluateurs purs) : `test_outcome_matches`, `test_trajectory_penalizes_unneeded_researcher`, `test_no_pr_when_ambiguous`.
- [ ] **Step 2–4:** FAIL → implémenter → PASS.
- [ ] **Step 5 (llm) :** `uv run python evals/run_evals.py --repetitions 2` → expérience visible dans LangSmith ; seuils de référence à noter dans `evals/README.md` : `outcome` = 1.0, `no_pr_when_ambiguous` = 1.0, `trajectory_subagents` ≥ 0.8, `criteria_covered` ≥ 0.8.
- [ ] **Step 6: Commit** `feat: offline LangSmith evaluation suite for all scenarios`

---

### Task 16: Smoke live, déploiement LangSmith, documentation

**Files:**
- Create: `scripts/smoke_live.py`, `README.md`, `docs/demo-guide.md`, `docs/architecture.md`
- Modify: `langgraph.json` si nécessaire

**Interfaces:**
- Consumes: tout.
- Produces:
  - `scripts/smoke_live.py <scenario>` : prend la clé dans `demo/seeded.json`, lance le CLI en mode local contre **vrai** Jira/GitHub/sandbox LangSmith ; l'humain décide aux interrupts ; à la fin affiche l'URL de PR et vérifie via `GitHubGateway`/Jira que la PR et le commentaire existent.
  - `README.md` (anglais) : pitch, diagramme d'architecture, tableau « Deep Agents building block → where it lives » (spec §4.4), setup (`.env`, ingestion, seed), commandes (`shipwright run`, `langgraph dev`, tests, évals), sécurité (D1, D2), limites connues.
  - `docs/demo-guide.md` : script de démo scénarios 2 → 4 → 6, avec les décisions à prendre aux interrupts et ce qu'il faut montrer dans Studio/GitHub/Jira.

- [ ] **Step 1 (live) :** `uv run python scripts/smoke_live.py 2` (un second lancement doit réutiliser la branche/PR existante, cf. Review Focus 6) → PR ouverte sur `taskboard-api`, CI GitHub Actions verte sur la PR, ticket commenté et en `In Review`.
- [ ] **Step 2 :** **Demander confirmation à l'utilisateur**, puis `langgraph deploy` (cf. skill `langgraph-cli`) vers LangSmith Deployment ; configurer les secrets de déploiement (toutes les variables de `.env` + `LS_MONGODB_URI` = URI Atlas avec nom de base, Atlas accessible depuis le data plane LangSmith).
- [ ] **Step 3 (live) :** depuis Studio connecté au déploiement, rejouer le scénario 2 sur un nouveau ticket → mêmes résultats ; vérifier que les checkpoints sont écrits dans MongoDB (collection du checkpointer) et l'audit dans `audit_log`.
- [ ] **Step 4 :** README + guides rédigés ; `uv run pytest` (unitaires) vert ; `uv run ruff check .` clean.
- [ ] **Step 5: Commit** `docs: README, architecture and demo guide; deployment config`
