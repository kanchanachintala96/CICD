# CI/CD Orchestrator Agent — New User Setup Guide

> **Scenario:** You are a developer with a Java project.  
> You want this agent to auto-detect your project, generate your CI/CD pipeline,  
> review your code with AI, and deploy it to Azure Container Apps.  
> You do it all by typing one sentence in **GitHub Copilot Chat**.

---

## What You Will Be Able to Do After This Setup

```
You type in Copilot Chat:
"Deploy my Java project at C:\work\my-java-app to Azure DevOps repo my-java-app"

Agent does automatically:
  ✅ Detects Java + Maven/Gradle
  ✅ Generates azure-pipelines.yml  (Build → Test → Docker → Deploy)
  ✅ Pushes your code to Azure Repos
  ✅ Creates and triggers the pipeline in Azure DevOps
  ✅ Deploys to Azure Container Apps
  ✅ Gives you the live pipeline URL
```

---

## What You Need Before Starting

### Software

| What | Minimum Version | How to Check |
|---|---|---|
| **Python** | 3.11 or 3.12 | `python --version` |
| **Node.js** | 18+ | `node --version` |
| **VS Code** | Latest | — |
| **GitHub Copilot extension** in VS Code | — | Extensions panel → search "GitHub Copilot" |

Download links if you are missing any:
- Python → [python.org/downloads](https://www.python.org/downloads/)
- Node.js → [nodejs.org](https://nodejs.org)
- VS Code → [code.visualstudio.com](https://code.visualstudio.com)

### Access / Accounts

| What | Where to Get It |
|---|---|
| **GitHub Copilot Business or Enterprise licence** | Ask your organisation admin |
| **EPAM AI DIAL API key** | Request from [dial-keys.lab.epam.com](https://dial-keys.lab.epam.com) |
| **Azure DevOps account** | [dev.azure.com](https://dev.azure.com) — free to create |
| **Azure subscription** | Only needed for the final "deploy to Azure" step |

---

## Step 1 — Get the Agent Folder

Get the `cicd-orchestrator-agent` folder from your team's shared repo:

```bash
git clone https://dev.azure.com/<your-org>/<project>/_git/cicd-orchestrator-agent
cd cicd-orchestrator-agent
```

If someone gave you a zip or a shared folder, just open it in a terminal.

---

## Step 2 — Install the Agent (One Time)

Open a terminal **inside the `cicd-orchestrator-agent` folder** and run these three commands:

```bash
# 1. Create an isolated Python environment
python -m venv venv

# 2. Activate it
.\venv\Scripts\activate           # Windows
# source venv/bin/activate        # macOS / Linux

# 3. Install all packages
pip install -e .
```

You should see `(venv)` in your terminal prompt.  
Verify it worked:
```bash
python -c "import flask, mcp, openai; print('All good!')"
# Expected:  All good!
```

---

## Step 3 — Set Up Your Credentials

### 3a — Create your `.env` file

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
```

Open `.env` in VS Code. Fill in the three sections below.

---

### 3b — EPAM AI DIAL key

This is needed for AI code review and AI pipeline analysis.

```env
DIAL_API_KEY=paste-your-key-here
DIAL_BASE_URL=https://ai-proxy.lab.epam.com
DIAL_MODEL=gpt-4o
```

> Once this is set, the agent uses it automatically. You never type it again.

---

### 3c — Azure DevOps

This is needed to create the pipeline and push your Java code.

```env
AZURE_DEVOPS_URL=https://dev.azure.com/your-org-name
AZURE_DEVOPS_PROJECT=YourProjectName
AZURE_DEVOPS_PAT=paste-your-pat-here
```

**Creating the PAT — takes 2 minutes:**

1. Open `https://dev.azure.com/<your-org>`
2. Click your **profile picture** (top right) → **Personal access tokens**
3. Click **+ New Token**
4. Give it a name (e.g. `cicd-agent`) and set an expiry
5. Under Scopes, select:
   - ✅ **Code** → Read & Write
   - ✅ **Build** → Read & Execute
6. Click **Create** and **copy the token immediately** — it shows only once
7. Paste it as `AZURE_DEVOPS_PAT` in `.env`

---

### 3d — Azure resources  *(skip this if you only want local pipeline runs)*

To deploy your Java app to **Azure Container Apps**, add these:

```env
AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_RESOURCE_GROUP=my-resource-group
AZURE_ACR_NAME=mycontainerregistry
AZURE_APP_NAME=my-java-app
AZURE_LOCATION=eastus
AZURE_ACA_ENV_NAME=my-aca-environment

AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=your-service-principal-secret
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**If you do not have these Azure resources yet**, run the following once with Azure CLI:

```bash
az login

# Resource Group
az group create --name my-resource-group --location eastus

# Container Registry (stores your Java Docker image)
az acr create --resource-group my-resource-group \
  --name mycontainerregistry --sku Basic --admin-enabled true

# Container Apps Environment (where your app will run)
az containerapp env create \
  --name my-aca-environment \
  --resource-group my-resource-group \
  --location eastus

# Service Principal (gives the pipeline permission to deploy)
az ad sp create-for-rbac \
  --name "cicd-agent-sp" \
  --role Contributor \
  --scopes /subscriptions/<subscription-id>/resourceGroups/my-resource-group
```

The last command outputs JSON — map it to your `.env`:

```
appId       →  AZURE_CLIENT_ID
password    →  AZURE_CLIENT_SECRET
tenant      →  AZURE_TENANT_ID
```

> ⚠️ `.env` is in `.gitignore` — it is **never committed** to any repo. Your credentials stay local.

---

## Step 4 — Connect the Agent to GitHub Copilot Chat

This step links the agent's 7 tools to your Copilot Chat.

### 4a — Open `.vscode/mcp.json` inside the agent folder

Find the `cicd-orchestrator` block and update **both paths** to match your machine:

```json
"cicd-orchestrator": {
  "type": "stdio",
  "command": "C:\\Users\\YourName\\cicd-orchestrator-agent\\venv\\Scripts\\python.exe",
  "args": [
    "C:\\Users\\YourName\\cicd-orchestrator-agent\\mcp_server.py"
  ],
  "env": {}
}
```

> **Windows:** use double backslashes `\\` in the path.  
> **Tip:** Copy the path from Windows Explorer address bar and replace each `\` with `\\`.

All credentials come from `.env` automatically — nothing else to add here.

### 4b — Reload VS Code and switch Copilot Chat to Agent mode

1. Press `Ctrl+Shift+P` → **Reload Window**
2. Open **GitHub Copilot Chat**
3. Switch to **Agent mode** (dropdown at the top of the chat panel)
4. You should now see the `cicd-orchestrator` tools available

---

## Step 5 — Use It with Your Java Project

You are ready. Type in Copilot Chat (Agent mode):

### First — detect your project

```
Detect my project at C:\work\my-java-app
```

Expected response:
```
✅ Detected: java-maven
   Test framework: junit
   Dockerfile: not found (will be auto-generated on deploy)
```

---

### Preview the pipeline

```
Preview the pipeline for my Java project at C:\work\my-java-app
```

Shows the exact steps — Build → Test → Deploy — before anything runs.

---

### Run the pipeline locally

```
Run the pipeline locally for my project at C:\work\my-java-app with tests
```

Runs `mvn clean package` (or `./gradlew build`) + your test suite on your machine.

---

### Export the Azure DevOps YAML

```
Export an Azure DevOps pipeline YAML for my Java project at C:\work\my-java-app
```

Returns a `azure-pipelines.yml` you can save and use independently.

---

### Deploy to Azure DevOps + Azure Container Apps

```
Deploy my Java project at C:\work\my-java-app to Azure DevOps repo my-java-app
```

The agent handles everything end-to-end:

| Step | What happens |
|---|---|
| 1 | Detects Java Maven or Gradle |
| 2 | Generates `azure-pipelines-aca.yml` with Build, Docker, Deploy stages |
| 3 | Creates a new Azure Repos repository named `my-java-app` |
| 4 | Pushes all your source files + the pipeline YAML in one commit |
| 5 | Creates the pipeline definition in Azure DevOps |
| 6 | Injects your Service Principal credentials as secure pipeline variables |
| 7 | Triggers the first pipeline run |
| 8 | Returns the live pipeline URL |

---

### Get AI code review

```
Review the code in my Java project at C:\work\my-java-app
```

Returns findings grouped by severity: Critical → High → Medium → Low.

---

### Check your run history

```
Show my recent pipeline runs
```

---

## Step 6 — Optional: Web Browser UI

If you prefer a browser over Copilot Chat:

```bash
.\venv\Scripts\activate
python app.py
```

Open `http://localhost:5000`

All fields (Azure DevOps, DIAL key, Azure resources) are **pre-filled from `.env`**.  
The UI has the same 5 capabilities: Pipeline, Export, Azure Deploy, AI Agent, Run History.

---

## Common Problems

| Problem | Fix |
|---|---|
| `python --version` shows 2.x | Install Python 3.11 from [python.org](https://python.org) |
| `(venv)` not showing in terminal | Run `.\venv\Scripts\activate` again — must be done every new terminal |
| `ModuleNotFoundError` | Re-run `pip install -e .` with venv active |
| Tools not appearing in Copilot Chat | Switch to **Agent mode**, not Chat mode. Reload VS Code window. |
| Paths not found in `mcp.json` | Use `\\` between each folder name on Windows |
| PAT error (401) | Regenerate PAT — check **Code Read+Write** + **Build Read+Execute** scopes |
| DIAL key error | Re-check the key at [dial-keys.lab.epam.com](https://dial-keys.lab.epam.com) |
| Azure deploy fails | Confirm all `AZURE_*` values in `.env` are correct — try `az login` |

---

> **For feature details and the full API reference** see [README.md](README.md).
