# CI/CD Orchestrator Agent

An **MCP-based CI/CD orchestrator agent** that detects your project type,
generates build + test + deploy pipelines, and can deploy them directly to
**Azure Container Apps** via Azure DevOps — usable from **GitHub Copilot Chat**
via natural language prompts, powered by **EPAM AI DIAL**.

> **New here?** → See **[SETUP.md](SETUP.md)** for the full step-by-step setup guide.

---

## Features

| Capability | Detail |
|---|---|
| **Multi-language detection** | Python, Node.js, Java (Maven/Gradle), Go, .NET, Ruby, Docker |
| **3-stage pipelines** | BUILD → TEST → DEPLOY |
| **Pipeline export** | GitHub Actions YAML, GitLab CI YAML, Jenkinsfile, Azure DevOps |
| **☁ Azure Deploy** | Pushes code to Azure Repos, creates & triggers pipeline — deploys to Azure Container Apps |
| **AI Agent (LLM + RAG)** | Reads your code files and uses an LLM to suggest the optimal pipeline |
| **AI Code Review** | EPAM AI DIAL-powered code review with severity-ranked findings — key auto-loaded from `.env` |
| **AI Pipeline Review** | LLM-powered pipeline analysis with priority-ranked suggestions |
| **Token usage tracking** | Visual budget meter, input/output token counts, cost estimate |
| **Run history** | SQLite-backed per-user pipeline run history |
| **Multi-user Web UI** | Browser-based, session-isolated, credentials auto-populated from `.env` |
| **MCP Server** | 7 tools exposed to GitHub Copilot Chat, Claude, Cursor via `mcp_server.py` |
| **Guardrails enforcement** | `guardrails.json` policy — blocks dangerous commands and hardcoded secrets at runtime |
| **Git hooks** | `pre-commit`, `commit-msg`, `pre-push` quality gates |

---

## Project Structure

```
cicd-orchestrator-agent/
├── app.py                          # Flask web app (REST API + UI)
├── main.py                         # CLI entry point
├── mcp_server.py                   # MCP server — 7 tools for GitHub Copilot Chat
├── guardrails.json                 # Protected agent policy contract (never modify via AI)
├── .dockerignore
├── .env                            # Your credentials (never commit)
├── .env.example                    # All environment variables documented
├── pyproject.toml
├── .github/
│   ├── copilot-instructions.md     # GitHub Copilot prompt → tool mapping + protected files
│   ├── ARCHITECTURE.md             # Full system architecture diagrams
│   ├── SKILLS.md                   # All 9 agent skills with prompts and prerequisites
│   ├── workflows/
│   │   └── ci.yml                  # GitHub Actions CI (test + lint — Python 3.11 & 3.12)
│   └── hooks/
│       ├── pre-commit              # Secret scanning + ruff lint + pytest
│       ├── commit-msg              # Conventional Commits format enforcement
│       └── pre-push                # Full test suite before remote push
├── .vscode/
│   └── mcp.json                    # MCP server config for GitHub Copilot / Claude / Cursor
├── templates/
│   └── index.html                  # Single-page Web UI (5 tabs)
├── src/cicd_orchestrator/
│   ├── project_detector.py         # Detect language from repo file indicators
│   ├── pipeline.py                 # Build/Test/Deploy pipeline data model + builders
│   ├── pipeline_exporter.py        # YAML export — GitHub Actions / GitLab CI / Jenkins / ADO
│   ├── azure_devops.py             # Azure DevOps REST API client (stdlib only)
│   ├── rag.py                      # RAG: reads code files → LLM context with token budget
│   ├── llm_agent.py                # LLM agent (EPAM DIAL + OpenAI)
│   ├── code_reviewer.py            # AI code review via EPAM AI DIAL
│   ├── guardrails.py               # Loads guardrails.json and enforces policies at runtime
│   ├── orchestrator.py             # Step executor (subprocess + preflight + guardrail check)
│   ├── workflow.py                 # Workflow runner + SQLite run tracking
│   ├── database.py                 # SQLite run history store
│   ├── cli.py                      # CLI commands (detect / pipeline / run)
│   ├── interactive_cli.py          # Interactive terminal mode
│   └── ui.py                       # ANSI terminal colour helpers
└── tests/
    └── test_orchestrator.py        # pytest test suite (24 tests, self-contained)
```

---

## Quick Start

> For the full setup including Azure, MCP, and org-wide deployment, see **[SETUP.md](SETUP.md)**.

```bash
python -m venv venv
.\venv\Scripts\activate          # Windows
# source venv/bin/activate       # macOS/Linux

pip install -e .
cp .env.example .env             # fill in DIAL_API_KEY + Azure credentials
python app.py                    # open http://localhost:5000
```

All Azure DevOps, Azure resource, and DIAL credentials are **auto-populated** in the
Web UI from `.env` on every page load — no manual entry needed.

---

## GitHub Copilot Chat (MCP)

This agent runs as an MCP server inside GitHub Copilot Chat. Configure `.vscode/mcp.json`:

```json
{
  "servers": {
    "cicd-orchestrator": {
      "type": "stdio",
      "command": "C:\\path\\to\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\cicd-orchestrator-agent\\mcp_server.py"]
    }
  }
}
```

Then use natural language prompts:

| Prompt | Tool used |
|---|---|
| `"Detect my project at C:\my-java-app"` | `detect_project` |
| `"Preview the pipeline for my project"` | `preview_pipeline` |
| `"Run the pipeline locally"` | `run_pipeline_locally` |
| `"Export GitHub Actions YAML for my project"` | `export_pipeline_yaml` |
| `"Deploy my project to ADO repo my-repo"` | `deploy_to_azure_devops` |
| `"Show my recent pipeline runs"` | `get_run_history` |
| `"List my Azure DevOps repositories"` | `list_ado_repos` |

See `.github/copilot-instructions.md` for the full prompt → tool mapping.

---

## Web UI Tabs

| Tab | What it does |
|---|---|
| **🔧 Pipeline** | Detect project type → configure options → preview → run locally |
| **📤 Export CI Config** | Generate GitHub Actions / GitLab CI / Jenkinsfile / Azure DevOps YAML |
| **☁ Azure Deploy** | Create Azure DevOps pipeline → push code → deploy to ACA automatically |
| **🤖 AI Agent** | LLM + RAG analysis with token budget control and cost tracking |
| **📋 Run History** | Browse past pipeline runs with step-level detail |

---

## Azure Deploy — How It Works

The **Azure Deploy** tab and `deploy_to_azure_devops` MCP tool create and trigger a real
Azure DevOps pipeline — no manual YAML editing needed.

### What the agent does automatically

1. Detects project type and generates `azure-pipelines-aca.yml`
2. Pushes all source files + pipeline YAML to Azure Repos (one commit)
3. Creates the pipeline definition in Azure DevOps
4. Sets Service Principal secrets as pipeline variables automatically
5. Triggers the first run and returns the live pipeline URL

### ADO Pipeline stages

```
Stage 1 → CI          : Build + Test (mvn / npm / go / dotnet)
Stage 2 → DockerBuild : docker build + push to Azure Container Registry
Stage 3 → Deploy      : az containerapp create/update → Azure Container Apps
```

### Prerequisites

Set these in `.env` once — the UI and MCP tools read them automatically:

```env
AZURE_DEVOPS_URL=https://dev.azure.com/myorg
AZURE_DEVOPS_PROJECT=MyProject
AZURE_DEVOPS_PAT=your-pat          # Code Read+Write, Build Read+Execute
AZURE_SUBSCRIPTION_ID=...
AZURE_RESOURCE_GROUP=my-rg
AZURE_ACR_NAME=myacr
AZURE_APP_NAME=my-app
AZURE_ACA_ENV_NAME=my-aca-env
AZURE_CLIENT_ID=...                # Service Principal
AZURE_CLIENT_SECRET=...
AZURE_TENANT_ID=...
```

---

## Guardrails

`guardrails.json` at the project root is the **protected policy contract** for this agent.
It is loaded at startup by `src/cicd_orchestrator/guardrails.py` and enforced at runtime.

**What it enforces:**
- Blocks dangerous commands before execution (`rm -rf /`, `curl | bash`, `eval`, `base64 -d | sh`)
- Scans generated content for hardcoded secrets (AWS keys, private keys, passwords)
- Validates pipeline stage and step counts against configured limits

> ⚠️ **Never modify `guardrails.json` via AI tools.** It is listed as a protected file in
> `.github/copilot-instructions.md` — GitHub Copilot will refuse to edit it.

---

## Git Hooks

Install once to enable local quality gates:

```bash
git config core.hooksPath .github/hooks
```

| Hook | Runs when | What it checks |
|---|---|---|
| `pre-commit` | Every `git commit` | Secret patterns, blocks `.env`, runs `ruff`, runs `pytest` |
| `commit-msg` | After writing commit message | Conventional Commits format (`feat/fix/ci/chore/...`) |
| `pre-push` | Before `git push` | Full test suite |

---

## AI Features

- **EPAM AI DIAL** (default): `gpt-4o` via `https://ai-proxy.lab.epam.com`
- **OpenAI** (optional fallback): `gpt-4o-mini`
- DIAL API key is **auto-loaded from `.env`** — never needs to be entered in the UI
- **Token Budget slider** (500–8000): how much code context is sent to the LLM
- **Max Output slider** (200–2000): max tokens in the LLM reply
- Token meter shows input / output / total / estimated USD cost
- RAG panel shows exactly which files were read

---

## CLI Usage

```bash
# Detect project type
python main.py detect --path ./my-project

# Preview pipeline
python main.py pipeline --path ./my-project --include-tests --include-lint

# Run pipeline
python main.py run --path ./my-project --include-tests

# Interactive terminal mode
python main.py interactive
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values. Key variables:

| Variable | Default | Description |
|---|---|---|
| `CICD_SECRET_KEY` | (random) | Flask session secret — set a strong value in production |
| `DIAL_API_KEY` | — | EPAM AI DIAL key — auto-loaded in UI, no manual entry needed |
| `DIAL_BASE_URL` | `https://ai-proxy.lab.epam.com` | DIAL endpoint |
| `DIAL_MODEL` | `gpt-4o` | Model on your DIAL instance |
| `OPENAI_API_KEY` | — | Optional OpenAI fallback |
| `AZURE_DEVOPS_URL` | — | ADO org URL (`https://dev.azure.com/myorg`) |
| `AZURE_DEVOPS_PROJECT` | — | ADO project name |
| `AZURE_DEVOPS_PAT` | — | Personal Access Token (Code R/W + Build R/Execute) |
| `AZURE_SUBSCRIPTION_ID` | — | Azure subscription ID |
| `AZURE_RESOURCE_GROUP` | — | Resource group for ACA deployment |
| `AZURE_ACR_NAME` | — | Azure Container Registry name |
| `AZURE_APP_NAME` | — | Container App name |
| `AZURE_LOCATION` | `eastus` | Azure region |
| `AZURE_ACA_ENV_NAME` | — | Container Apps environment name |
| `AZURE_CLIENT_ID` | — | Service Principal app ID |
| `AZURE_CLIENT_SECRET` | — | Service Principal secret |
| `AZURE_TENANT_ID` | — | Azure AD tenant ID |
| `PORT` | `5000` | Web server port |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |