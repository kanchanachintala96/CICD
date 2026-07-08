# CI/CD Orchestrator Agent — Architecture

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER INTERFACES                              │
│                                                                     │
│   ┌─────────────────┐        ┌──────────────────────────────────┐  │
│   │   Web UI         │        │   MCP Server (mcp_server.py)     │  │
│   │ localhost:5000   │        │   stdio / HTTP SSE               │  │
│   │ (Flask + HTML)   │        │   GitHub Copilot / Claude Code   │  │
│   └────────┬─────────┘        │   Cursor / Claude Desktop        │  │
│            │ REST API          └──────────────┬───────────────────┘  │
└────────────┼──────────────────────────────────┼─────────────────────┘
             │                                  │
             ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      AGENT CORE  (app.py)                           │
│                                                                     │
│  /api/detect        /api/pipeline      /api/run                     │
│  /api/export        /api/pipeline/review   /api/azure/config        │
│  /api/azure/repos   /api/azure/deploy-aca  /api/history             │
└──────────┬───────────────────┬──────────────────┬───────────────────┘
           │                   │                  │
           ▼                   ▼                  ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐
│  Project         │ │  Pipeline        │ │  Azure DevOps            │
│  Detector        │ │  Engine          │ │  Client                  │
│                  │ │                  │ │                          │
│  project_        │ │  pipeline.py     │ │  azure_devops.py         │
│  detector.py     │ │  orchestrator.py │ │                          │
│                  │ │  workflow.py     │ │  • Push code to repo     │
│  • Detects type  │ │  pipeline_       │ │  • Create pipeline def   │
│  • Java/Python/  │ │  exporter.py     │ │  • Set SP variables      │
│    Node/Go/etc   │ │                  │ │  • Trigger pipeline run  │
│  • has_tests     │ │  • Build steps   │ └──────────────────────────┘
│  • has_dockerfile│ │  • Test steps    │
│  • has_compose   │ │  • Deploy steps  │ ┌──────────────────────────┐
└──────────────────┘ │  • YAML export   │ │  AI Review               │
                     │  • Local run     │ │                          │
                     └──────────────────┘ │  rag.py                  │
                                          │  llm_agent.py            │
┌──────────────────┐                      │  code_reviewer.py        │
│  Run History     │                      │                          │
│  database.py     │                      │  • RAG reads project     │
│  SQLite store    │                      │  • Sends to DIAL/LLM     │
└──────────────────┘                      │  • Pipeline suggestions  │
                                          │  • Code review findings  │
                                          └──────────────────────────┘
```

---

## Low-Level Architecture

### 1. Project Detection Layer
```
detect_project_type(path)
        │
        ├── pyproject.toml / setup.py / requirements.txt  → python
        ├── package.json                                   → nodejs
        ├── pom.xml                                        → java-maven
        ├── build.gradle                                   → java-gradle
        ├── go.mod                                         → go
        ├── *.csproj / *.sln                               → dotnet
        ├── Gemfile                                        → ruby
        └── Dockerfile (fallback)                         → docker

detect_project_info(path)
        │
        ├── project_type
        ├── has_tests       (tests/ dir, test_*.py, *_test.go, src/test/)
        ├── has_dockerfile  (Dockerfile exists)
        ├── has_docker_compose
        ├── has_lint_config (.flake8, .eslintrc, .golangci.yml)
        └── nodejs_scripts  (scripts from package.json)
```

### 2. Pipeline Engine Layer
```
build_pipeline(project_type, path, options)
        │
        ├── PipelineOptions
        │       ├── include_tests    (bool)
        │       ├── include_lint     (bool)
        │       ├── extra_commands   (list of shell commands)
        │       ├── retry            (int)
        │       ├── deploy_type      (docker | kubernetes | script)
        │       └── deploy_env       (staging | production)
        │
        ├── Language builders
        │       ├── _python_steps()   → pip install, compileall, pytest
        │       ├── _java_maven_steps() → mvn package, mvn test
        │       ├── _nodejs_steps()   → npm ci, npm run build, npm test
        │       ├── _go_steps()       → go mod download, go build, go test
        │       ├── _dotnet_steps()   → dotnet restore, build, test
        │       └── _docker_steps()   → docker build, smoke test
        │
        └── Pipeline object
                ├── build_steps[]
                ├── test_steps[]
                ├── deploy_steps[]
                └── cleanup_steps[]
```

### 3. Pipeline Exporter Layer
```
export_pipeline(pipeline, target)
        │
        ├── github-actions   → .github/workflows/pipeline.yml
        ├── gitlab-ci        → .gitlab-ci.yml
        ├── jenkins          → Jenkinsfile (declarative)
        ├── azure-devops-basic    → azure-pipelines.yml
        │                           (CI only: Build + Test stages)
        └── azure-devops-aca      → azure-pipelines-aca.yml
                                    Stage 1: CI  (build + test)
                                    Stage 2: DockerBuild
                                             ├── Assign AcrPush/AcrPull to SP
                                             ├── docker build
                                             └── docker push → ACR
                                    Stage 3: Deploy
                                             ├── az containerapp create/update
                                             └── --no-wait (non-blocking)
```

### 4. Local Execution Layer
```
execute_step(step, cwd)
        │
        ├── Windows wrapper resolution (before preflight)
        │       ├── ./mvnw  → resolves mvnw.cmd full path on Windows
        │       └── ./gradlew → resolves gradlew.bat full path on Windows
        │
        ├── _preflight_check(command)
        │       ├── tool on PATH?  (mvn, npm, go, dotnet, docker)
        │       ├── Java wrapper?  → skip PATH check
        │       └── Java installed? (for Maven/Gradle)
        │
        ├── JAVA_HOME injection  (auto-detected from well-known paths)
        │
        └── subprocess.run(command, shell=True, cwd=cwd)
                ├── success → ExecutionResult(success=True)
                └── failure → retry up to step.retry times
                              → ExecutionResult(success=False, output=...)
```

### 5. Azure DevOps Integration Layer
```
AzureDevOpsClient(org_url, project, pat)
        │
        ├── validate_connection()        GET /_apis/projects/{project}
        ├── list_repositories()          GET /_apis/git/repositories
        ├── get_repository(name)         finds by name in list
        │
        ├── push_file(repo_id, path, content, branch)
        │       └── single file commit (add or edit)
        │
        ├── push_directory(repo_id, local_path, branch, extra_files)
        │       ├── walks local directory (skips .git, node_modules, venv)
        │       ├── skips files > 500KB
        │       ├── binary files → base64 encoded
        │       ├── detects add vs edit per file
        │       └── single commit with all files
        │
        ├── create_pipeline(name, repo_id, yaml_path, branch)
        │       └── POST /_apis/pipelines
        │
        ├── set_pipeline_variables(pipeline_id, variables)
        │       ├── GET /_apis/build/definitions/{id}  (fetch full definition)
        │       ├── strip read-only fields (_links, authoredBy, etc.)
        │       ├── merge SP secret variables (isSecret=True)
        │       └── PUT /_apis/build/definitions/{id}  (update definition)
        │
        └── run_pipeline(pipeline_id, branch)
                └── POST /_apis/pipelines/{id}/runs
```

### 6. AI Review Layer
```
Code Review (on Detect)
        └── code_reviewer.py
                ├── RAG reads: README, src files, requirements, pom.xml
                ├── DIAL API call with code-review prompt
                └── Returns: overall_rating, summary, findings[]
                           (severity: critical/high/medium/low/info)

Pipeline Review (on Preview)
        └── llm_agent.py → review_pipeline_with_llm()
                ├── rag.py reads project files (token budget: 3000)
                ├── Pipeline steps formatted as text
                ├── DIAL API call with pipeline-review prompt
                └── Returns: score/10, summary, suggestions[]
                           (priority: high/medium/low)
                           (category: security/performance/reliability/
                                      best-practice/coverage)
```

### 7. MCP Server Layer
```
mcp_server.py  (FastMCP — stdio or HTTP SSE)
        │
        ├── detect_project(path)
        │       └── → project_detector.detect_project_info()
        │
        ├── preview_pipeline(path, project_type, include_tests)
        │       └── → pipeline.build_pipeline()
        │
        ├── run_pipeline_locally(path, project_type)
        │       └── → workflow.Workflow.execute()
        │
        ├── export_pipeline_yaml(path, target)
        │       └── → pipeline_exporter.export_*()
        │
        ├── deploy_to_azure_devops(repo_name, project_type, ...)
        │       ├── → pipeline_exporter.export_azure_devops_aca_sp()
        │       ├── → AzureDevOpsClient.push_directory()
        │       ├── → AzureDevOpsClient.create_pipeline()
        │       ├── → AzureDevOpsClient.set_pipeline_variables()
        │       └── → AzureDevOpsClient.run_pipeline()
        │
        ├── get_run_history(limit)
        │       └── → database.get_db().list_runs()
        │
        └── list_ado_repos()
                └── → AzureDevOpsClient.list_repositories()
```

### 8. Data Flow — End-to-End Deploy
```
User provides: local project path + ADO repo name
         │
         ▼
1. detect_project_type(path)           → "java-maven"
         │
         ▼
2. build_pipeline("java-maven", path)  → Pipeline(build+test+deploy steps)
         │
         ▼
3. export_azure_devops_aca_sp(pipeline, rg, acr, app, env)
         │                             → azure-pipelines-aca.yml (in memory)
         ▼
4. AzureDevOpsClient.push_directory()  → pushes all source files + YAML
         │                               to ADO Git repo (one commit)
         ▼
5. AzureDevOpsClient.create_pipeline() → creates pipeline definition in ADO
   (or reuses existing by name)
         │
         ▼
6. AzureDevOpsClient.set_pipeline_variables()
         │                             → sets AZURE_CLIENT_ID/SECRET/
         │                               TENANT_ID/SUBSCRIPTION_ID as secrets
         ▼
7. AzureDevOpsClient.run_pipeline()    → triggers first run
         │
         ▼
ADO Pipeline executes:
  Stage 1 (CI)          → mvn clean package + mvn test
  Stage 2 (DockerBuild) → az acr login + docker build + docker push → ACR
  Stage 3 (Deploy)      → az containerapp create/update --no-wait
         │
         ▼
App running at: https://kanchana-app.xxx.eastus2.azurecontainerapps.io
```

---

## Configuration (.env)

| Variable | Purpose |
|---|---|
| `AZURE_DEVOPS_URL` | ADO org URL |
| `AZURE_DEVOPS_PROJECT` | ADO project name |
| `AZURE_DEVOPS_PAT` | Personal Access Token (Code R/W + Build R/Execute) |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_RESOURCE_GROUP` | Resource group name |
| `AZURE_ACR_NAME` | Container registry name |
| `AZURE_APP_NAME` | Container app name |
| `AZURE_LOCATION` | Azure region (e.g. eastus2) |
| `AZURE_ACA_ENV_NAME` | Container Apps environment name |
| `AZURE_CLIENT_ID` | Service Principal app ID |
| `AZURE_CLIENT_SECRET` | Service Principal client secret |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `DIAL_API_KEY` | EPAM AI DIAL API key (for AI review) |
| `DIAL_BASE_URL` | DIAL endpoint URL |
| `DIAL_MODEL` | Model name (e.g. gpt-4o) |

---

## Project Structure

```
cicd-orchestrator-agent/
├── app.py                        # Flask web application + REST API
├── mcp_server.py                 # MCP server (7 tools)
├── pyproject.toml                # Package metadata + dependencies
├── .env                          # Credentials and resource config
├── .env.example                  # Config template
├── templates/
│   └── index.html                # Single-page Web UI
├── src/cicd_orchestrator/
│   ├── project_detector.py       # Language + feature detection
│   ├── pipeline.py               # Pipeline data model + step builders
│   ├── pipeline_exporter.py      # YAML export (GitHub/GitLab/Jenkins/ADO)
│   ├── orchestrator.py           # Local step execution engine
│   ├── workflow.py               # Workflow runner + DB tracking
│   ├── azure_devops.py           # Azure DevOps REST API client
│   ├── database.py               # SQLite run history
│   ├── rag.py                    # RAG context builder for LLM
│   ├── llm_agent.py              # LLM pipeline analysis + pipeline review
│   ├── code_reviewer.py          # AI code review (DIAL)
│   ├── cli.py                    # CLI entry point
│   ├── interactive_cli.py        # Interactive terminal UI
│   └── ui.py                     # ANSI terminal helpers
├── tests/
│   └── test_orchestrator.py      # pytest test suite
└── .vscode/
    └── mcp.json                  # MCP server config for VS Code
```
