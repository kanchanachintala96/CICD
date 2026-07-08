# CI/CD Orchestrator Agent — Skills

This document lists all skills the agent can perform, how to trigger them via the
**Web UI** and via the **MCP server** (GitHub Copilot, Claude Code, Cursor).

---

## Skill 1 — Detect Project

Analyses a local project folder and identifies the language, framework, and setup.

**What it detects:**
- Project type: Java (Maven / Gradle), Python, Node.js, Go, .NET, Ruby, Docker
- Has tests (test directory, test files)
- Has Dockerfile
- Has docker-compose.yml
- Has lint configuration

**Web UI:**
> Pipeline tab → enter project path → click **Detect**

**MCP prompt:**
```
Detect my project at C:\Users\me\my-java-app
```

---

## Skill 2 — AI Code Review

Reads source files via RAG and reviews the code quality using EPAM AI DIAL.
Returns findings ranked by severity (Critical / High / Medium / Low / Info).

**Web UI:**
> Pipeline tab → Detect → click **Review Code** → enter DIAL API key

**MCP prompt:**
```
Review the code in my project at C:\Users\me\my-java-app
```

---

## Skill 3 — Preview Pipeline

Generates tailored CI/CD pipeline steps without executing anything.
Shows build, test, and deploy steps based on detected project type.

**Web UI:**
> Pipeline tab → Detect → Configure → click **Preview Pipeline**

**MCP prompt:**
```
Preview the CI/CD pipeline for my project at C:\Users\me\my-java-app
```

---

## Skill 4 — AI Pipeline Review

Reviews the configured pipeline steps using RAG + EPAM AI DIAL.
Suggests improvements with priority and category tags.

**Categories:** Security · Performance · Reliability · Best Practice · Coverage

**Web UI:**
> Pipeline tab → Preview → enter DIAL API key → click **Review Pipeline with AI**

**MCP prompt:**
```
Review the pipeline for my project at C:\Users\me\my-java-app and suggest improvements
```

---

## Skill 5 — Run Pipeline Locally

Executes the pipeline steps (build + test) on the local machine.
Handles Maven wrapper (mvnw.cmd on Windows), JAVA_HOME injection, and retries.

**Web UI:**
> Pipeline tab → Preview → click **Run Pipeline Locally**

**MCP prompt:**
```
Run the pipeline locally for my project at C:\Users\me\my-java-app
```

---

## Skill 6 — Export CI Config

Generates a ready-to-commit CI/CD configuration file for any platform.

**Supported targets:**

| Target | Output file |
|---|---|
| Azure DevOps — CI only | `azure-pipelines.yml` |
| Azure DevOps — Full ACA Deploy | `azure-pipelines-aca.yml` |
| GitHub Actions | `.github/workflows/pipeline.yml` |
| GitLab CI | `.gitlab-ci.yml` |
| Jenkins | `Jenkinsfile` |

**Web UI:**
> Export CI Config tab → enter path → select target → click **Generate Config**

**MCP prompt:**
```
Export an Azure DevOps pipeline YAML for my project at C:\Users\me\my-java-app
```

---

## Skill 7 — Deploy to Azure DevOps

Full end-to-end deployment in one action:
1. Pushes local code to Azure Git Repos
2. Generates `azure-pipelines-aca.yml`
3. Creates the pipeline definition in ADO
4. Sets Service Principal secret variables automatically
5. Triggers the first pipeline run

**ADO pipeline stages:**
```
Stage 1 → CI          : Build + Test (mvn/npm/go/dotnet)
Stage 2 → DockerBuild : Build image + push to ACR
Stage 3 → Deploy      : az containerapp create/update → Azure Container Apps
```

**Web UI:**
> Azure Deploy tab → Connect → pick repo + project type → click **Create & Trigger Pipeline**

OR

> Pipeline tab → Preview → Deploy to Azure DevOps → enable ACA toggle → fill fields → click **Create & Trigger Pipeline**

**MCP prompt:**
```
Deploy my Java project at C:\Users\me\my-java-app to Azure DevOps repo my-repo
```

---

## Skill 8 — List ADO Repositories

Lists all Git repositories in the configured Azure DevOps project.

**Web UI:**
> Azure Deploy tab → click **Connect & Load Repos**

**MCP prompt:**
```
List my Azure DevOps repositories
```

---

## Skill 9 — View Run History

Shows recent local pipeline execution history with pass/fail status per step.

**Web UI:**
> Run History tab

**MCP prompt:**
```
Show my recent pipeline runs
```

---

## MCP Tool Reference

Add to `.vscode/mcp.json` to use from GitHub Copilot, Claude Code, or Cursor:

```json
{
  "servers": {
    "cicd-orchestrator": {
      "type": "stdio",
      "command": "C:\\path\\to\\cicd-orchestrator-agent\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\cicd-orchestrator-agent\\mcp_server.py"]
    }
  }
}
```

| MCP Tool | Description |
|---|---|
| `detect_project` | Detect language, tests, Dockerfile from a local path |
| `preview_pipeline` | Generate pipeline steps without running |
| `run_pipeline_locally` | Execute build + test on local machine |
| `export_pipeline_yaml` | Export CI config for any platform |
| `deploy_to_azure_devops` | Full deploy: push → pipeline → set vars → trigger |
| `get_run_history` | Recent local pipeline run history |
| `list_ado_repos` | List repos in the ADO project |

---

## Pre-requisites

| Requirement | Details |
|---|---|
| Azure Subscription | Contributor access |
| Azure DevOps | Project with PAT (Code R/W + Build R/Execute) |
| Service Principal | Created by admin with Contributor role on subscription |
| Azure Container Registry | Created in Azure Portal |
| ACA Environment | Created in Azure Portal |
| DIAL API Key | For AI code review and pipeline review features |

All credentials configured once in `.env` — auto-populated in the UI on every page load.

---

## Supported Project Types

| Type | Detected by | Build command | Test command |
|---|---|---|---|
| Java Maven | `pom.xml` | `mvn clean package` | `mvn test` |
| Java Gradle | `build.gradle` | `gradle assemble` | `gradle test` |
| Python | `pyproject.toml` / `requirements.txt` | `pip install` | `pytest` |
| Node.js | `package.json` | `npm run build` | `npm test` |
| Go | `go.mod` | `go build ./...` | `go test ./...` |
| .NET | `*.csproj` / `*.sln` | `dotnet build` | `dotnet test` |
| Ruby | `Gemfile` | `bundle install` | `bundle exec rspec` |
| Docker | `Dockerfile` | `docker build` | container smoke test |
