# Shipwright — Document de conception

*Date : 2026-11-05 · Statut : prêt pour validation*

## 1. Intention

Shipwright est un agent **Deep Agents** (LangChain) qui prend un ticket Jira et le mène jusqu'à une Pull Request GitHub prête à merger, sous contrôle humain.

**Pourquoi ce projet :** démontrer, sur un cas concret et utile en entreprise, la maîtrise de **toutes les briques décrites dans la doc Deep Agents** — planification, sous-agents, MCP, tools, backends, permissions, skills, human-in-the-loop, middleware, mémoire, context schema, durabilité, observabilité, évaluation, déploiement — chacune ayant une vraie raison d'être, pas plaquée.

**Moment "wow" de la démo :** une PR propre apparaît sur GitHub à partir d'un ticket Jira (critères d'acceptation cochés, rapport de review, tests verts), et le ticket Jira est commenté avec le lien.

## 2. Objectifs, non-objectifs, anti-objectifs

**Objectifs**
- Traiter des tickets simples à moyens (petites features, bugfix) sur un repo démo FastAPI.
- Suivi visible en temps réel : plan (todos), sous-agents actifs, résultats de tests, interruptions HITL.
- Gérer proprement les chemins non-nominaux (ticket ambigu, tests qui échouent, ticket trop gros, rejet/modification humaine).
- Code de qualité "entreprise" : config typée, tests, évals, traces, déployable sur LangSmith Deployment.

**Non-objectifs (v1)**
- Grosses refontes multi-modules.
- Répondre aux commentaires de review sur la PR après ouverture.
- Interface web custom (LangSmith Studio sert d'UI en v1 ; une UI `useStream` pourra venir plus tard).
- Confluence (l'utilisateur n'en a pas).
- Multi-agents / plateforme (Code Review agent, etc. : plus tard).

**Anti-objectifs (échec même si "ça marche")**
- L'agent écrit sur `main`, ou agit sur GitHub/Jira **sans validation humaine**.
- L'agent invente une implémentation quand le ticket est ambigu au lieu de demander.
- Boucles infinies (retries de tests, allers-retours reviewer) sans sortie propre.
- Secrets lisibles par l'agent (dans la sandbox ou les fichiers).
- Briques ajoutées uniquement "pour cocher la case" sans rôle réel.

## 3. Ressources et contraintes

| Ressource | Disponibilité | Usage |
|---|---|---|
| OpenAI | ✅ clé | Modèles LLM (fort pour orchestrateur/développeur, léger pour chercheur/reviewer — via `.env`) |
| LangSmith | ✅ compte | Traces, Studio (UI démo), évals, sandbox (`LangSmithSandbox`), déploiement |
| MongoDB Atlas | ✅ | Checkpointer, Store (mémoire long terme), journal d'audit |
| Jira (Atlassian) | ✅ — **nouveau projet à créer** (clé proposée `SHIP`) | Tickets démo ; lecture/commentaire/transition/création de sous-tickets via Atlassian MCP |
| GitHub | ✅ | **Nouveau repo démo FastAPI à créer** ; branches/PR via GitHub MCP |
| Pinecone | ✅ | Index sémantique de la base de connaissances (specs, ADR) |
| LlamaIndex (LlamaParse) | ✅ | Parsing des PDF/Markdown de `knowledge/` avant indexation |
| Tavily | ✅ | Recherche web (docs de libs, bonnes pratiques) |
| Confluence | ❌ | Remplacé par `knowledge/` (PDF/MD) + PR/CHANGELOG + commentaire Jira |

**Contraintes techniques**
- Python, gestion via `uv`.
- Deep Agents **SDK open source** (`create_deep_agent`) — *pas* Managed Deep Agents (choix : contrôle et démonstration de chaque brique, portabilité).
- Déploiement cible : **LangSmith Deployment** via `langgraph.json`.
- `FilesystemBackend` / `LocalShellBackend` interdits en déploiement (cf. doc "Going to production").
- Toujours vérifier l'API courante dans la doc live (docs.langchain.com) : les APIs Deep Agents évoluent vite.

## 4. Architecture (décidée)

### 4.1 Agents

**Orchestrateur** (agent principal)
- Lit le ticket Jira, planifie (`write_todos`), délègue via `task`, décide du chemin.
- **Seul détenteur des outils d'écriture externes** : création de branche/commit/PR (GitHub MCP), commentaires/transitions/création de tickets (Atlassian MCP). Tous soumis à `interrupt_on`.

**Sous-agents** (chacun avec ses propres tools, prompt, et éventuellement modèle) :

| Sous-agent | Rôle | Tools | Droits |
|---|---|---|---|
| `researcher` | Contexte : specs/ADR internes + connaissance externe | `knowledge_search` (Pinecone), `web_search` (Tavily) | Lecture seule ; aucun accès au code |
| `code_analyst` | Comprendre le code concerné, localiser les fichiers, proposer une approche | Filesystem sur le **clone du repo dans la sandbox** (`ls`, `read_file`, `grep`, `glob`) | Lecture seule (permissions) |
| `developer` | Implémenter + écrire/lancer les tests | Filesystem + `execute` dans la sandbox | Écriture **uniquement** dans le workspace du repo cloné |
| `reviewer` | Auto-review du diff vs critères d'acceptation + skills (conventions, sécurité) ; rapport structuré | Filesystem lecture seule sur le clone | Lecture seule |

**Décision clé :** le code est lu sur un **clone dans la sandbox**, pas fichier par fichier via GitHub MCP (lent et coûteux). GitHub MCP sert aux opérations GitHub (branche, push/PR, issues).

### 4.2 Flux nominal
```
Ticket Jira ─► Orchestrateur : lire ticket, vérifier critères d'acceptation, plan
   ├─► researcher (si spec référencée ou besoin externe)   ┐ parallèle possible
   └─► code_analyst                                         ┘
   └─► developer : implémente + tests (sandbox)
   └─► reviewer : conforme ? sinon retour developer (max 2 cycles)
─► HITL : diff + tests + rapport reviewer → approuver / modifier / refuser
─► branche + PR (description : critères cochés, rapport reviewer, lien ticket) + CHANGELOG
─► HITL : commentaire Jira avec lien PR + transition statut
```

### 4.3 Scénarios à supporter

| # | Scénario | Comportement attendu | Mis en scène en démo |
|---|---|---|---|
| 1 | Bugfix simple | analyst → developer → reviewer → PR (pas de researcher) | |
| 2 | Feature avec spec dans `knowledge/` | researcher ∥ analyst → developer → reviewer → PR | ⭐ (le "wow") |
| 3 | Besoin de connaissance externe | researcher utilise Tavily + Pinecone | |
| 4 | Ticket ambigu / sans critères | HITL pour commenter Jira avec questions précises ; **stop**, pas de code | ⭐ (crédibilité) |
| 5 | Tests échouent | max N tentatives du developer, puis HITL : continuer avec indice / abandonner / PR draft | |
| 6 | Humain demande une modification au HITL | feedback renvoyé au developer → nouveau cycle → HITL | ⭐ (HITL) |
| 7 | Ticket trop gros | HITL pour proposer et créer des sous-tickets Jira ; pas de PR | |

### 4.4 Correspondance briques Deep Agents

| Brique | Mise en œuvre |
|---|---|
| Planification | `write_todos` (orchestrateur) |
| Sous-agents | 4 sous-agents ci-dessus, tools scopés |
| MCP | GitHub MCP + Atlassian MCP (via `langchain-mcp-adapters`) |
| Tools custom | `knowledge_search` (Pinecone), `web_search` (Tavily), éventuellement helpers repo |
| Backends | `CompositeBackend` : `StateBackend` (scratch thread) + `StoreBackend` sur `/memories/` (MongoDB, namespacé par utilisateur) + sandbox pour le workspace repo |
| Sandbox | `LangSmithSandbox`, **scope thread**, TTL d'inactivité ; secrets via auth proxy (jamais en env/fichier dans la sandbox) |
| Permissions | règles allow/deny : analyst/reviewer lecture seule ; developer écriture dans le workspace ; `/policies/` lecture seule |
| Skills | `coding-conventions`, `pr-template`, `jira-workflow`, `testing-guidelines` (SKILL.md) ; donnés explicitement aux sous-agents concernés (non hérités) |
| Human-in-the-loop | `interrupt_on` sur tous les outils d'écriture GitHub/Jira ; actions approve/edit/reject |
| Middleware | `PIIMiddleware` (redact email/secrets en entrée), audit (log de chaque tool call d'écriture dans MongoDB), `ModelCallLimitMiddleware`, retries/fallback modèle |
| Mémoire | `/memories/` par utilisateur (préférences, conventions apprises) |
| Context schema | `user_id`, `repo` (owner/name), `jira_project` |
| Durabilité | Checkpointer MongoDB (local/self-host) ; checkpointer géré en LangSmith Deployment |
| Observabilité | LangSmith tracing (`LANGSMITH_TRACING`, `LANGSMITH_PROJECT`) |
| Évaluation | `evals/` : jeu de tickets démo + évaluateurs LangSmith (trajectoire : bons sous-agents appelés ; résultat : tests verts, critères couverts ; scénario 4 : pas de PR) |
| Déploiement | `langgraph.json` → LangSmith Deployment |

### 4.5 Structure du repo Shipwright
```
shipwright/
├── pyproject.toml / langgraph.json / .env.example
├── src/shipwright/
│   ├── agent.py          # assemble create_deep_agent (factory async si sandbox par thread)
│   ├── config.py         # settings typés (pydantic-settings)
│   ├── context.py
│   ├── prompts/
│   ├── subagents/        # researcher, code_analyst, developer, reviewer
│   ├── tools/            # knowledge_search, web_search
│   ├── mcp/              # clients GitHub + Atlassian
│   ├── backends.py
│   ├── permissions.py
│   └── middleware/       # pii, audit, limits
├── skills/
├── knowledge/            # specs/ADR PDF+MD du repo démo (source pour Pinecone)
├── scripts/ingest_docs.py  # LlamaParse → chunks → Pinecone
├── scripts/seed_jira.py    # création des tickets démo
├── tests/ · evals/ · docs/
```

### 4.6 Repo démo (cible des PR)
Nouveau repo GitHub, petit service **FastAPI** avec tests pytest et CI simple (GitHub Actions lançant les tests). Assez de code pour que l'analyse ait du sens (quelques routers, modèles, services), assez petit pour rester rapide. Le domaine fonctionnel est au choix du builder (proposition : gestion de tâches/projets "taskboard-api"). Les specs des features démo sont dans `knowledge/` côté Shipwright et référencées par les tickets.

### 4.7 Tickets démo (projet Jira `SHIP`)
Au minimum un ticket par scénario mis en scène (2, 4, 6), plus 1, 3, 5, 7 pour les évals. Créés par `scripts/seed_jira.py`.

### 4.8 Décisions de conception (tranchées après revue — *choix de l'assistant*, à vérifier par l'utilisateur)

**D1 — Points de validation humaine (HITL).** Trois interruptions par ticket nominal, pas une par appel d'outil :
1. **Review gate** (outil custom `request_human_review` qui appelle `interrupt()`) : présente diff + résultats de tests + rapport reviewer. Réponses : `approve` / `feedback` (texte libre → renvoyé au developer pour un nouveau cycle, scénario 6) / `reject` (stop propre).
2. **Publication GitHub** : un seul outil composite `publish_pull_request` (branche + fichiers + CHANGELOG + PR) gardé par `interrupt_on` (approve/reject, arguments visibles).
3. **Mise à jour Jira** : un seul outil composite `update_jira_ticket` (commentaire + transition) gardé par `interrupt_on`.
Les scénarios 4 et 7 utilisent aussi `update_jira_ticket` / création de sous-tickets, gardés par `interrupt_on`. Les outils MCP d'écriture bruts ne sont **pas** exposés directement au modèle : seuls les outils composites (qui les appellent) le sont.

**D2 — Frontière de sécurité sandbox → GitHub.** La sandbox n'a **aucun credential d'écriture GitHub**. Le repo démo est cloné en lecture (repo public ou token read-only injecté par l'auth proxy). Les commandes réseau git en écriture (`git push`) ne sont pas possibles faute de credentials. Après approbation, l'orchestrateur lit les fichiers modifiés depuis la sandbox (`download_files` / diff) et `publish_pull_request` pousse via **GitHub MCP/API** côté serveur. Toute écriture GitHub passe donc par un outil gardé par HITL.

**D3 — Évaluations.** Deux niveaux :
- **Offline (CI, reproductible)** : MCP GitHub/Jira **mockés** (fakes renvoyant des fixtures), repo démo fixture, sandbox réelle ou locale de dev, interruptions **auto-résolues** par le harness d'éval (`Command(resume=...)` scripté par scénario). Évaluateurs : trajectoire (sous-agents attendus appelés), résultat (tests verts, critères couverts), scénario 4 → aucun appel à `publish_pull_request`.
- **Live smoke** : exécution manuelle d'un ticket réel (scénario 2) sur le vrai Jira/GitHub avant chaque démo.

**D4 — MongoDB (vérifié dans la doc "Configure checkpointer backend").** 
- **Local / CLI** : MongoDB Atlas pour le **checkpointer** (`MongoDBSaver`), le **Store** (`MongoDBStore`) et le **journal d'audit**.
- **LangSmith Deployment** : **checkpointer MongoDB** via `langgraph.json` (`"checkpointer": {"backend": "mongo"}` + `LS_MONGODB_URI`, Agent Server ≥ 0.7.64, Atlas = replica set OK) ; le **Store** est celui de la plateforme (Postgres, la doc ne documente pas de Store Mongo en déploiement) ; le **journal d'audit** reste dans MongoDB.

**D4b — Contraintes techniques vérifiées dans la doc (2026-11-05).**
- Les *permissions* ne couvrent que les outils filesystem intégrés, **pas** `execute` ni les outils MCP/custom. Avec un `CompositeBackend` dont le défaut est une sandbox, chaque chemin de permission doit tomber sous une **route déclarée** (`/**` lève `NotImplementedError`). Donc : permissions appliquées sur `/memories/` et `/policies/` ; la lecture seule de `code_analyst` et `reviewer` sur le repo est imposée par un **middleware qui retire les outils d'écriture et `execute`** de leurs appels modèle.
- `backend=` reçoit une instance (les fabriques `lambda` sont dépréciées) ; les `namespace=lambda rt: ...` restent valides. Le namespace mémoire utilise `rt.context.user_id` (fonctionne en local comme en serveur).
- Les policies vivent sur une route dédiée `/policies/` (StoreBackend, namespace organisation), lecture seule par permission ; les skills sont **uploadées dans la sandbox** (`/skills/`) à la création du workspace.
- Le repo cible vient de la config (`SHIPWRIGHT_REPO`, surcharge possible via `configurable.repo`) ; le `context_schema` porte `user_id` et `jira_project`.

**D5 — Contenu de la mémoire.**
- `/memories/preferences.md` (par utilisateur, écriture par l'agent) : l'agent y consigne les **retours humains réutilisables** reçus au review gate (ex. « utiliser des dataclasses, pas des dicts »). Au ticket suivant, il le lit en début de run → démontre visiblement la mémoire (le feedback du scénario 6 est appliqué automatiquement plus tard).
- `/policies/` (organisation, **lecture seule** via permissions, seedé par `scripts/seed_policies.py`) : règles d'entreprise non négociables (fichiers interdits à modifier, ex. `.github/workflows/`, règles de sécurité, taille max d'une PR). Différence avec les skills : les **policies** sont des contraintes toujours appliquées ; les **skills** sont des procédures (« comment écrire une PR ») chargées à la demande.

**D6 — « Tests verts ».** Signifie le **pytest dans la sandbox** avant le review gate. La CI GitHub Actions du repo démo tourne sur la PR (visible pour la démo) mais Shipwright ne la surveille pas en v1.

**D7 — Langue.** Tout ce que produit ou lit l'agent est en **anglais** : prompts, skills, descriptions de PR, CHANGELOG, commentaires Jira, tickets démo, rubriques d'évaluation, README. *(décision utilisateur)*

**D8 — Calendrier et priorités.** Pas d'échéance : l'objectif est un projet **propre et complet** plutôt que rapide *(décision utilisateur)*. Ordre de construction recommandé, chaque étape testée avant la suivante :
1. Fondations : config, repo démo FastAPI, projet Jira `SHIP`, ingestion `knowledge/` → Pinecone.
2. Orchestrateur + sous-agents + sandbox : scénario 1 (bugfix) de bout en bout jusqu'au review gate.
3. Publication HITL (GitHub + Jira) → scénario 2 complet (le "wow").
4. Scénarios non nominaux 4, 5, 6, 7.
5. Mémoire, policies, permissions, middleware (PII, audit, limites).
6. Évaluations offline + smoke live.
7. Déploiement LangSmith Deployment + README/guide de démo.

## 5. Déclenchement
v1 : l'utilisateur lance Shipwright depuis **LangSmith Studio** (ou CLI/SDK) avec un message du type « Traite SHIP-42 ». Pas de webhook Jira en v1 (piste future).

## 6. Laissé au builder
- Choix exact des serveurs MCP GitHub/Atlassian (officiels distants vs locaux) selon l'auth disponible, tant que les opérations listées sont couvertes.
- Noms exacts des modèles OpenAI (fort / léger), configurables.
- Domaine fonctionnel et contenu du repo démo.
- Stratégie de chunking/metadata pour Pinecone.
- Valeurs de limites (N retries, TTL sandbox, model call limit).
- Mécanisme de clone/push du repo dans la sandbox (token via auth proxy).
- Si les sandboxes LangSmith ne sont pas disponibles sur le plan : fallback vers un autre provider supporté (Daytona, Modal, Runloop).

## 7. Pistes futures (hors v1)
UI `useStream` · webhook Jira (déclenchement auto) · cron rapport de sprint · agent Code Review partageant les briques · réponse aux commentaires de review sur la PR.
