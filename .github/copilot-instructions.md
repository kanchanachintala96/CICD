# CI/CD Orchestrator Agent — Copilot Instructions

This file teaches GitHub Copilot how to use the CI/CD Orchestrator Agent MCP server.
When the user asks to detect, build, test, or deploy their project, use the
`cicd-orchestrator` MCP tools listed below.

---

## MCP Server: cicd-orchestrator

The agent is connected via `mcp.json`. It can detect project types, generate pipelines,
run them locally, and deploy to Azure DevOps with Azure Container Apps.

All Azure credentials (ADO PAT, Service Principal, ACR, Resource Group, etc.) are
pre-configured in the agent's `.env` file — no need to ask the user for them.

---

## Prompts → Tools Mapping

### Detect Project
**When user says:** "detect my project", "what type is my project", "analyse my code"

```
Use tool: detect_project
Parameters:
  path: <local path to the project>

Example prompt:
  "Detect my project at C:\Users\me\sample-ecommerce"
```

---

### Preview Pipeline
**When user says:** "preview pipeline", "show me the pipeline steps", "what pipeline will be generated"

```
Use tool: preview_pipeline
Parameters:
  path: <local path to the project>
  project_type: (optional) java-maven | java-gradle | python | nodejs | go | dotnet | docker
  include_tests: true (default)
  include_lint: false (default)
  extra_commands: (optional) list of additional shell commands

Example prompt:
  "Preview the CI/CD pipeline for my project at C:\Users\me\sample-ecommerce"
  "Show me pipeline steps for my Java project, include linting"
```

---

### Run Pipeline Locally
**When user says:** "run pipeline locally", "build and test my project", "validate my code locally"

```
Use tool: run_pipeline_locally
Parameters:
  path: <local path to the project>
  project_type: (optional)
  include_tests: true (default)

Example prompt:
  "Run the pipeline locally for my project at C:\Users\me\sample-ecommerce"
  "Build and test my Java project at C:\Users\me\sample-ecommerce"
```

---

### Export Pipeline YAML
**When user says:** "export pipeline", "generate YAML", "give me the azure-pipelines.yml", "generate CI config"

```
Use tool: export_pipeline_yaml
Parameters:
  path: <local path to the project>
  target: azure-devops-aca | azure-devops-basic | github-actions | gitlab-ci | jenkins
  project_type: (optional)
  include_tests: true (default)

Example prompts:
  "Export the Azure DevOps ACA pipeline YAML for my project at C:\Users\me\sample-ecommerce"
  "Generate a GitHub Actions pipeline for my Java project at C:\Users\me\sample-ecommerce"
  "Give me the azure-pipelines.yml for C:\Users\me\sample-ecommerce"
```

---

### Deploy to Azure DevOps
**When user says:** "deploy", "push to Azure", "create pipeline in ADO", "trigger pipeline", "deploy to Azure Container Apps"

```
Use tool: deploy_to_azure_devops
Parameters:
  repo_name: <ADO repository name>
  project_type: java-maven | java-gradle | python | nodejs | go | dotnet | docker
  path: (optional) local project path — leave blank if code is already in ADO repo
  branch: main (default)
  pipeline_name: "Deploy to Azure Container Apps" (default)
  skip_provision: true (set true if RG, ACR, ACA environment already exist in Azure)
  include_tests: true (default)

Example prompts:
  "Deploy my Java project at C:\Users\me\sample-ecommerce to Azure DevOps repo sample-ecommerce"
  "Create and trigger the ADO pipeline for my project — repo name is sample-ecommerce"
  "Push my code to Azure Repos and deploy to Container Apps"

NOTE: After calling this tool, the ADO pipeline will automatically:
  1. Build and test the code
  2. Build Docker image and push to Azure Container Registry
  3. Deploy to Azure Container Apps
```

---

### List ADO Repositories
**When user says:** "list repos", "show my Azure DevOps repos", "what repos do I have"

```
Use tool: list_ado_repos
Parameters: none

Example prompt:
  "List my Azure DevOps repositories"
  "What repos are available in my ADO project?"
```

---

### View Run History
**When user says:** "show run history", "recent pipeline runs", "what pipelines ran"

```
Use tool: get_run_history
Parameters:
  limit: 10 (default)

Example prompt:
  "Show my recent pipeline runs"
  "What were the last 5 pipeline executions?"
```

---

## Full End-to-End Flow

When the user asks to do everything from detection to deployment, chain the tools:

```
Step 1 → detect_project(path)
          — identifies language, tests, Dockerfile

Step 2 → preview_pipeline(path, project_type)
          — shows what the pipeline will look like

Step 3 → run_pipeline_locally(path, project_type)   [optional]
          — validates build + test before cloud deploy

Step 4 → deploy_to_azure_devops(repo_name, project_type, path)
          — pushes code + creates pipeline + sets SP variables + triggers run
```

**Example prompt for full flow:**
```
"Detect my project at C:\Users\me\sample-ecommerce, preview the pipeline,
 then deploy it to Azure DevOps repo sample-ecommerce"
```

---

## ⚠️ Protected Files — NEVER Modify

The following files are **protected policy and infrastructure files**. GitHub Copilot
must **never modify, overwrite, rewrite, or delete** them under any circumstances.
Any change to these files requires explicit human approval only.

| File | Why it is protected |
|---|---|
| `guardrails.json` | Agent policy contract — defines what the agent can and cannot do at runtime. Changing it could allow dangerous commands, expose secrets, or bypass compliance rules. |
| `.github/copilot-instructions.md` | Copilot's own instruction set — must not be self-modified. |
| `.github/hooks/pre-commit` | Security hook that blocks secrets from being committed. |
| `.github/hooks/commit-msg` | Enforces Conventional Commits format. |
| `.env` | Live credentials — must never be read aloud, logged, or modified. |

If a user asks you to modify `guardrails.json`, respond:
> "guardrails.json is a protected policy file. Changes to it require human review and cannot be made by Copilot."

---

## Important Notes for Copilot

- **Credentials are pre-configured** — never ask the user for ADO PAT, ACR name,
  resource group, or SP credentials. The agent reads them from `.env` automatically.

- **skip_provision should be true** when Azure resources (Resource Group, ACR,
  Container Apps Environment) already exist. Set false only for a brand new Azure setup.

- **project_type** values: `java-maven`, `java-gradle`, `python`, `nodejs`,
  `go`, `dotnet`, `ruby`, `docker`

- **target** values for export: `azure-devops-aca`, `azure-devops-basic`,
  `github-actions`, `gitlab-ci`, `jenkins`

- If the user has not provided a project path, ask: **"What is the local path to your project?"**

- If the user has not provided a repo name for deploy, ask: **"What is the Azure DevOps repository name?"**
