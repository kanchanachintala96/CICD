"""
Pipeline exporters — convert a Pipeline into CI platform YAML / config files.

Supported targets:
  • GitHub Actions  (.github/workflows/pipeline.yml)
  • GitLab CI       (.gitlab-ci.yml)
  • Jenkinsfile     (Declarative Pipeline syntax)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import Pipeline

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# ── GitHub Actions ──────────────────────────────────────────────────────────

def export_github_actions(pipeline: "Pipeline", branch: str = "main") -> str:
    """Return a GitHub Actions workflow YAML string for *pipeline*."""
    from .pipeline import PipelineStage

    build_steps = pipeline.build_steps
    test_steps = pipeline.test_steps
    deploy_steps = pipeline.deploy_steps

    def _step(ps) -> dict:
        s = {"name": ps.name, "run": ps.command}
        if ps.env:
            s["env"] = ps.env
        if ps.allow_failure:
            s["continue-on-error"] = True
        return s

    jobs: dict = {}

    if build_steps:
        jobs["build"] = {
            "runs-on": "ubuntu-latest",
            "steps": [
                {"uses": "actions/checkout@v4"},
                *[_step(s) for s in build_steps],
            ],
        }

    if test_steps:
        test_job: dict = {
            "runs-on": "ubuntu-latest",
            "steps": [
                {"uses": "actions/checkout@v4"},
                *[_step(s) for s in test_steps],
            ],
        }
        if build_steps:
            test_job["needs"] = ["build"]
        jobs["test"] = test_job

    if deploy_steps:
        deploy_job: dict = {
            "runs-on": "ubuntu-latest",
            "environment": "production",
            "steps": [
                {"uses": "actions/checkout@v4"},
                *[_step(s) for s in deploy_steps],
            ],
        }
        needs = []
        if build_steps:
            needs.append("build")
        if test_steps:
            needs.append("test")
        if needs:
            deploy_job["needs"] = needs
        jobs["deploy"] = deploy_job

    workflow = {
        "name": f"CI/CD Pipeline ({pipeline.project_type})",
        "on": {
            "push": {"branches": [branch]},
            "pull_request": {"branches": [branch]},
        },
        "jobs": jobs,
    }

    return _to_yaml(workflow)


# ── GitHub Actions → trigger an Azure DevOps pipeline run ──────────────────

def export_github_actions_trigger_ado(
    org_url: str,
    project: str,
    pipeline_id: int,
    branch: str = "main",
    workflow_name: str = "Trigger Azure DevOps Pipeline",
) -> str:
    """
    Return a GitHub Actions workflow YAML that, on push to *branch*, calls the
    Azure DevOps REST API to queue a run of an *existing* ADO pipeline
    (identified by *pipeline_id*) instead of building/deploying in GitHub Actions.

    The workflow expects a repository secret named ADO_PAT (an Azure DevOps
    Personal Access Token with "Build (Read & execute)" scope) to be configured
    in GitHub → Settings → Secrets and variables → Actions.
    """
    org = org_url.rstrip("/")
    run_url = f"{org}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1-preview.1"

    workflow = {
        "name": workflow_name,
        "on": {"push": {"branches": [branch]}},
        "jobs": {
            "trigger-ado-pipeline": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {
                        "name": "Queue Azure DevOps pipeline run",
                        "env": {"ADO_PAT": "${{ secrets.ADO_PAT }}"},
                        "run": (
                            "curl -sS -f -u \":$ADO_PAT\" -X POST "
                            f'"{run_url}" '
                            "-H \"Content-Type: application/json\" "
                            "-d '{\"resources\": {\"repositories\": {\"self\": "
                            f'{{"refName": "refs/heads/{branch}"}}'
                            "}}}'"
                        ),
                    },
                ],
            }
        },
    }
    return _to_yaml(workflow)


# ── GitLab CI ───────────────────────────────────────────────────────────────

def export_gitlab_ci(pipeline: "Pipeline") -> str:
    """Return a .gitlab-ci.yml YAML string for *pipeline*."""
    from .pipeline import PipelineStage

    stages = []
    if pipeline.build_steps:
        stages.append("build")
    if pipeline.test_steps:
        stages.append("test")
    if pipeline.deploy_steps:
        stages.append("deploy")

    config: dict = {"stages": stages}

    for step in pipeline.build_steps:
        job_key = _slugify(step.name)
        config[job_key] = {
            "stage": "build",
            "script": [step.command],
        }
        if step.retry:
            config[job_key]["retry"] = step.retry
        if step.allow_failure:
            config[job_key]["allow_failure"] = True

    for step in pipeline.test_steps:
        job_key = _slugify(step.name)
        config[job_key] = {
            "stage": "test",
            "script": [step.command],
        }
        if step.retry:
            config[job_key]["retry"] = step.retry

    for step in pipeline.deploy_steps:
        job_key = _slugify(step.name)
        config[job_key] = {
            "stage": "deploy",
            "script": [step.command],
            "when": "manual",
            "environment": {"name": "production"},
        }
        if step.env:
            config[job_key]["variables"] = step.env

    return _to_yaml(config)


# ── Jenkinsfile ─────────────────────────────────────────────────────────────

def export_jenkinsfile(pipeline: "Pipeline") -> str:
    """Return a declarative Jenkinsfile string for *pipeline*."""
    lines = [
        "pipeline {",
        "    agent any",
        "    stages {",
    ]

    def _add_stage(stage_name: str, steps):
        lines.append(f"        stage('{stage_name}') {{")
        lines.append("            steps {")
        for s in steps:
            safe_cmd = s.command.replace("'", "\\'")
            lines.append(f"                sh '{safe_cmd}'")
        lines.append("            }")
        lines.append("        }")

    if pipeline.build_steps:
        _add_stage("Build", pipeline.build_steps)
    if pipeline.test_steps:
        _add_stage("Test", pipeline.test_steps)
    if pipeline.deploy_steps:
        lines.append("        stage('Deploy') {")
        lines.append("            when { branch 'main' }")
        lines.append("            steps {")
        for s in pipeline.deploy_steps:
            safe_cmd = s.command.replace("'", "\\'")
            lines.append(f"                sh '{safe_cmd}'")
        lines.append("            }")
        lines.append("        }")

    lines += [
        "    }",
        "    post {",
        "        always {",
        "            echo 'Pipeline finished.'",
        "        }",
        "        failure {",
        "            echo 'Pipeline failed!'",
        "        }",
        "    }",
        "}",
    ]

    return "\n".join(lines)


# ── helpers ─────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_-]", "_", text.lower()).strip("_")


def _to_yaml(data: dict) -> str:
    if _YAML_AVAILABLE:
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    # Minimal fallback — only handles simple cases
    return _simple_yaml(data, indent=0)


def _simple_yaml(obj, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_simple_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(obj, list):
        lines = []
        for item in obj:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    prefix = f"{pad}- " if first else f"{pad}  "
                    first = False
                    if isinstance(v, (dict, list)):
                        lines.append(f"{prefix}{k}:")
                        lines.append(_simple_yaml(v, indent + 2))
                    else:
                        lines.append(f"{prefix}{k}: {v}")
            else:
                lines.append(f"{pad}- {item}")
        return "\n".join(lines)
    return str(obj)


# ── Azure DevOps — Azure Container Apps ──────────────────────────────────────

def export_azure_devops_aca(
    pipeline: "Pipeline",
    acr_name: str,
    resource_group: str,
    app_name: str,
    service_connection: str = "azure-service-connection",
    branch: str = "main",
) -> str:
    """
    Generate an azure-pipelines.yml that:
      Stage 1 (CI)     — project build + test steps, then az acr build → ACR
      Stage 2 (Deploy) — az containerapp update + health check
    """
    image_name = app_name.lower().replace(" ", "-")

    lines = [
        "# Generated by CI/CD Orchestrator Agent",
        "# Deploys to Azure Container Apps via Azure DevOps",
        "",
        "trigger:",
        "  branches:",
        "    include:",
        f"      - {branch}",
        "",
        "pr: none",
        "",
        "variables:",
        f"  ACR_NAME: '{acr_name}'",
        f"  RESOURCE_GROUP: '{resource_group}'",
        f"  APP_NAME: '{app_name}'",
        f"  SERVICE_CONNECTION: '{service_connection}'",
        "  IMAGE_TAG: $(Build.BuildId)",
        f"  IMAGE_FULL: $(ACR_NAME).azurecr.io/{image_name}:$(IMAGE_TAG)",
        "",
        "stages:",
        "",
        "  # ── Stage 1: Build, Test & push Docker image to ACR ─────────────────",
        "  - stage: CI",
        "    displayName: Build, Test & Push",
        "    jobs:",
        "      - job: BuildTestPush",
        "        displayName: Build, test and push image",
        "        pool:",
        "          vmImage: ubuntu-latest",
        "        steps:",
        "          - checkout: self",
    ]

    for step in pipeline.build_steps:
        lines.append("")
        lines.append("          - script: |")
        for cmd_line in step.command.splitlines():
            lines.append(f"              {cmd_line}")
        lines.append(f"            displayName: '{step.name.replace(chr(39), '')}'")
        if step.allow_failure:
            lines.append("            continueOnError: true")

    for step in pipeline.test_steps:
        lines.append("")
        lines.append("          - script: |")
        for cmd_line in step.command.splitlines():
            lines.append(f"              {cmd_line}")
        lines.append(f"            displayName: '{step.name.replace(chr(39), '')}'")
        if step.allow_failure:
            lines.append("            continueOnError: true")

    lines += [
        "",
        "          - task: AzureCLI@2",
        "            displayName: Build and push Docker image to ACR",
        "            inputs:",
        "              azureSubscription: $(SERVICE_CONNECTION)",
        "              scriptType: bash",
        "              scriptLocation: inlineScript",
        "              inlineScript: |",
        "                set -euo pipefail",
        "                az acr build \\",
        "                  --registry \"$(ACR_NAME)\" \\",
        f"                  --image \"{image_name}:$(IMAGE_TAG)\" \\",
        f"                  --image \"{image_name}:latest\" \\",
        "                  --platform linux/amd64 \\",
        "                  .",
        "",
        "  # ── Stage 2: Deploy to Azure Container Apps ─────────────────────────",
        "  - stage: Deploy",
        "    displayName: Deploy to Azure Container Apps",
        "    dependsOn: CI",
        "    condition: succeeded()",
        "    jobs:",
        "      - deployment: DeployACA",
        "        displayName: Update Container App",
        "        pool:",
        "          vmImage: ubuntu-latest",
        "        environment: production",
        "        strategy:",
        "          runOnce:",
        "            deploy:",
        "              steps:",
        "",
        "                - task: AzureCLI@2",
        "                  displayName: Deploy new image to Container App",
        "                  inputs:",
        "                    azureSubscription: $(SERVICE_CONNECTION)",
        "                    scriptType: bash",
        "                    scriptLocation: inlineScript",
        "                    inlineScript: |",
        "                      set -euo pipefail",
        "                      az containerapp update \\",
        "                        --name \"$(APP_NAME)\" \\",
        "                        --resource-group \"$(RESOURCE_GROUP)\" \\",
        "                        --image \"$(IMAGE_FULL)\"",
        "                      echo \"Deployed $(IMAGE_FULL) to $(APP_NAME)\"",
        "",
        "                - task: AzureCLI@2",
        "                  displayName: Health check",
        "                  inputs:",
        "                    azureSubscription: $(SERVICE_CONNECTION)",
        "                    scriptType: bash",
        "                    scriptLocation: inlineScript",
        "                    inlineScript: |",
        "                      FQDN=$(az containerapp show \\",
        "                        --name \"$(APP_NAME)\" \\",
        "                        --resource-group \"$(RESOURCE_GROUP)\" \\",
        "                        --query \"properties.configuration.ingress.fqdn\" \\",
        "                        -o tsv)",
        "                      APP_URL=\"https://${FQDN}\"",
        "                      echo \"App URL: ${APP_URL}\"",
        "                      for i in $(seq 1 10); do",
        "                        STATUS=$(curl -sk -o /dev/null -w \"%{http_code}\" \\",
        "                          \"${APP_URL}\" || echo 000)",
        "                        echo \"Health check ${i}/10: HTTP ${STATUS}\"",
        "                        [ \"$STATUS\" = \"200\" ] && \\",
        "                          echo \"App is live at ${APP_URL}\" && exit 0",
        "                        sleep 10",
        "                      done",
        "                      echo \"Health check failed\" && exit 1",
    ]

    return "\n".join(lines) + "\n"


# ── Azure DevOps — Basic CI ───────────────────────────────────────────────────

def export_azure_devops_basic(
    pipeline: "Pipeline",
    pipeline_name: str = "CI Pipeline",
    branch: str = "main",
) -> str:
    """
    Generate a minimal azure-pipelines.yml that runs CI (build + test) steps.
    No ACR / Container Apps setup required — just push and run.
    Automatically adds Java setup for Maven/Gradle projects.
    """
    is_java = pipeline.project_type in ("java-maven", "java-gradle")
    uses_mvnw = any(
        "./mvnw" in (s.command or "") for s in pipeline.steps
    )

    lines = [
        "# Generated by CI/CD Orchestrator Agent",
        f"# Pipeline: {pipeline_name}",
        "",
        "trigger:",
        "  branches:",
        "    include:",
        f"      - {branch}",
        "",
        "pr:",
        "  branches:",
        "    include:",
        f"      - {branch}",
        "",
        "stages:",
    ]

    def _job_header(job_name: str) -> list:
        header = [
            f"      - job: {job_name}",
            f"        displayName: {job_name}",
            "        pool:",
            "          vmImage: ubuntu-latest",
            "        steps:",
            "          - checkout: self",
        ]
        if is_java:
            header += [
                "",
                "          - task: JavaToolInstaller@0",
                "            displayName: 'Set up Java 17'",
                "            inputs:",
                "              versionSpec: '17'",
                "              jdkArchitectureOption: 'x64'",
                "              jdkSourceOption: 'PreInstalled'",
            ]
        if uses_mvnw:
            header += [
                "",
                "          - script: chmod +x ./mvnw",
                "            displayName: 'Make mvnw executable'",
            ]
        return header

    def _script_steps(steps):
        out = []
        for step in steps:
            out.append("")
            out.append("          - script: |")
            for cmd_line in step.command.splitlines():
                out.append(f"              {cmd_line}")
            out.append(f"            displayName: '{step.name.replace(chr(39), '')}'")
            if step.allow_failure:
                out.append("            continueOnError: true")
        return out

    if pipeline.build_steps:
        lines += [
            "",
            "  - stage: Build",
            "    displayName: Build",
            "    jobs:",
        ]
        lines += _job_header("BuildJob")
        lines += _script_steps(pipeline.build_steps)

    if pipeline.test_steps:
        lines += [
            "",
            "  - stage: Test",
            "    displayName: Test",
        ]
        if pipeline.build_steps:
            lines += ["    dependsOn: Build", "    condition: succeeded()"]
        lines += ["    jobs:"]
        lines += _job_header("TestJob")
        lines += _script_steps(pipeline.test_steps)

    if pipeline.deploy_steps:
        depends_on = []
        if pipeline.build_steps:
            depends_on.append("Build")
        if pipeline.test_steps:
            depends_on.append("Test")
        lines += [
            "",
            "  - stage: Deploy",
            "    displayName: Deploy",
        ]
        if depends_on:
            lines += [f"    dependsOn: [{', '.join(depends_on)}]", "    condition: succeeded()"]
        lines += ["    jobs:"]
        lines += _job_header("DeployJob")
        lines += _script_steps(pipeline.deploy_steps)

    if not pipeline.build_steps and not pipeline.test_steps and not pipeline.deploy_steps:
        lines += [
            "",
            "  - stage: CI",
            "    displayName: CI",
            "    jobs:",
        ]
        lines += _job_header("CIJob")
        lines += _script_steps(pipeline.steps)

    return "\n".join(lines) + "\n"


# ── dispatch ─────────────────────────────────────────────────────────────────

EXPORTERS = {
    "github-actions": export_github_actions,
    "gitlab-ci": export_gitlab_ci,
    "jenkins": export_jenkinsfile,
}


def export_pipeline(pipeline: "Pipeline", target: str, **kwargs) -> str:
    """Export *pipeline* to the given *target* format."""
    exporter = EXPORTERS.get(target)
    if exporter is None:
        raise ValueError(f"Unknown export target '{target}'. Choose from: {list(EXPORTERS)}")
    return exporter(pipeline, **kwargs)


# ── Azure DevOps — Azure Container Apps (Service Principal, no service connection) ──

def export_azure_devops_aca_sp(
    pipeline: "Pipeline",
    resource_group: str,
    acr_name: str,
    app_name: str,
    location: str = "eastus",
    aca_env_name: str = "",
    branch: str = "main",
    pipeline_name: str = "Deploy to Azure Container Apps",
    skip_provision: bool = False,
) -> str:
    """
    Generate azure-pipelines.yml that:
      Stage 1 (CI)     — build + test (Maven/Gradle/etc.)
      Stage 2 (Infra)  — az login with SP, create RG + ACR + ACA environment if needed
      Stage 3 (Build)  — docker build + push to ACR
      Stage 4 (Deploy) — az containerapp create/update

    Uses Azure Service Principal credentials stored as pipeline secret variables:
      AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID

    No ADO service connection required.
    """
    image_name = app_name.lower().replace(" ", "-").replace("_", "-")
    aca_env = aca_env_name or f"{app_name}-env"

    lines = [
        "# Generated by CI/CD Orchestrator Agent",
        f"# Pipeline: {pipeline_name}",
        "# Deploys to Azure Container Apps using Service Principal auth",
        "#",
        "# Required pipeline secret variables (set in ADO → Pipelines → Variables):",
        "#   AZURE_CLIENT_ID       — Service Principal Application (client) ID",
        "#   AZURE_CLIENT_SECRET   — Service Principal client secret",
        "#   AZURE_TENANT_ID       — Azure AD Tenant ID",
        "#   AZURE_SUBSCRIPTION_ID — Azure Subscription ID",
        "",
        "trigger:",
        "  branches:",
        "    include:",
        f"      - {branch}",
        "",
        "pr: none",
        "",
        "variables:",
        f"  RESOURCE_GROUP:  '{resource_group}'",
        f"  ACR_NAME:        '{acr_name}'",
        f"  APP_NAME:        '{app_name}'",
        f"  ACA_ENV:         '{aca_env}'",
        f"  LOCATION:        '{location}'",
        "  IMAGE_TAG:       $(Build.BuildId)",
        f"  IMAGE_FULL:      $({acr_name.upper().replace('-','_') if False else 'ACR_NAME'}).azurecr.io/{image_name}:$(IMAGE_TAG)",
        "",
        "stages:",
    ]

    # ── Stage 1: CI (build + test) ─────────────────────────────────────────────
    ci_steps = list(pipeline.build_steps) + list(pipeline.test_steps)
    if not ci_steps:
        ci_steps = list(pipeline.steps)

    is_java = pipeline.project_type in ("java-maven", "java-gradle")

    lines += [
        "",
        "  # ── Stage 1: Build & Test ────────────────────────────────────────────",
        "  - stage: CI",
        "    displayName: Build and Test",
        "    jobs:",
        "      - job: CIJob",
        "        displayName: Build and test application",
        "        pool:",
        "          vmImage: ubuntu-latest",
        "        steps:",
        "          - checkout: self",
    ]

    if is_java:
        lines += [
            "",
            "          - task: JavaToolInstaller@0",
            "            displayName: Set up Java 17",
            "            inputs:",
            "              versionSpec: '17'",
            "              jdkArchitectureOption: x64",
            "              jdkSourceOption: PreInstalled",
        ]

    for step in ci_steps:
        lines += [
            "",
            "          - script: |",
        ]
        for cmd_line in step.command.splitlines():
            lines.append(f"              {cmd_line}")
        lines.append(f"            displayName: '{step.name.replace(chr(39), '')}'")
        if step.allow_failure:
            lines.append("            continueOnError: true")

    # ── Stage 2: Provision infra (skipped if resources already exist) ────────────
    if not skip_provision:
     lines += [
        "",
        "  # ── Stage 2: Provision Azure Infrastructure ─────────────────────────",
        "  - stage: Provision",
        "    displayName: Provision Azure Infrastructure",
        "    dependsOn: CI",
        "    condition: succeeded()",
        "    jobs:",
        "      - job: ProvisionJob",
        "        displayName: Create RG, ACR, ACA environment",
        "        pool:",
        "          vmImage: ubuntu-latest",
        "        steps:",
        "          - checkout: none",
        "",
        "          - script: |",
        "              set -euo pipefail",
        "              az login --service-principal \\",
        "                --username  \"$(AZURE_CLIENT_ID)\" \\",
        "                --password  \"$(AZURE_CLIENT_SECRET)\" \\",
        "                --tenant    \"$(AZURE_TENANT_ID)\"",
        "              az account set --subscription \"$(AZURE_SUBSCRIPTION_ID)\"",
        "            displayName: 'Azure login (Service Principal)'",
        "",
        "          - script: |",
        "              set -euo pipefail",
        "              # Resource Group",
        "              az group create --name \"$(RESOURCE_GROUP)\" --location \"$(LOCATION)\" --output none",
        "              echo \"✓ Resource group: $(RESOURCE_GROUP)\"",
        "",
        "              # Azure Container Registry (admin disabled — SP used for auth)",
        "              az acr create \\",
        "                --resource-group \"$(RESOURCE_GROUP)\" \\",
        "                --name          \"$(ACR_NAME)\" \\",
        "                --sku           Basic \\",
        "                --admin-enabled false \\",
        "                --output none 2>/dev/null || echo '✓ ACR already exists'",
        "              echo \"✓ ACR: $(ACR_NAME).azurecr.io\"",
        "",
        "              # Grant the Service Principal AcrPush + AcrPull on the ACR",
        "              ACR_ID=$(az acr show --name \"$(ACR_NAME)\" --resource-group \"$(RESOURCE_GROUP)\" --query id -o tsv)",
        "              SP_OID=$(az ad sp show --id \"$(AZURE_CLIENT_ID)\" --query id -o tsv 2>/dev/null || echo '')",
        "              if [ -n \"$SP_OID\" ]; then",
        "                az role assignment create --assignee-object-id \"$SP_OID\" \\",
        "                  --assignee-principal-type ServicePrincipal \\",
        "                  --role AcrPush --scope \"$ACR_ID\" --output none 2>/dev/null || true",
        "                az role assignment create --assignee-object-id \"$SP_OID\" \\",
        "                  --assignee-principal-type ServicePrincipal \\",
        "                  --role AcrPull --scope \"$ACR_ID\" --output none 2>/dev/null || true",
        "                echo \"✓ AcrPush + AcrPull roles assigned to Service Principal\"",
        "              fi",
        "",
        "              # Container Apps extension + environment",
        "              az extension add --name containerapp --upgrade --only-show-errors 2>/dev/null || true",
        "              az provider register --namespace Microsoft.App --wait --output none 2>/dev/null || true",
        "              az provider register --namespace Microsoft.OperationalInsights --wait --output none 2>/dev/null || true",
        "              az containerapp env create \\",
        "                --name           \"$(ACA_ENV)\" \\",
        "                --resource-group \"$(RESOURCE_GROUP)\" \\",
        "                --location       \"$(LOCATION)\" \\",
        "                --output none 2>/dev/null || echo '✓ ACA environment already exists'",
        "              echo \"✓ ACA environment: $(ACA_ENV)\"",
        "            displayName: 'Create resource group, ACR and ACA environment'",
     ]

    # ── Stage 3: Docker build + push ───────────────────────────────────────────
    docker_depends = "CI" if skip_provision else "Provision"
    lines += [
        "",
        "  # ── Stage 3: Docker Build & Push ────────────────────────────────────",
        "  - stage: DockerBuild",
        "    displayName: Build and Push Docker Image",
        f"    dependsOn: {docker_depends}",
        "    condition: succeeded()",
        "    jobs:",
        "      - job: DockerJob",
        "        displayName: Build image and push to ACR",
        "        pool:",
        "          vmImage: ubuntu-latest",
        "        steps:",
        "          - checkout: self",
        "",
        "          - script: |",
        "              set -euo pipefail",
        "              az login --service-principal \\",
        "                --username  \"$(AZURE_CLIENT_ID)\" \\",
        "                --password  \"$(AZURE_CLIENT_SECRET)\" \\",
        "                --tenant    \"$(AZURE_TENANT_ID)\"",
        "              az account set --subscription \"$(AZURE_SUBSCRIPTION_ID)\"",
        "            displayName: 'Azure login (Service Principal)'",
        "",
        "          - script: |",
        "              set -euo pipefail",
        "              # Ensure SP has AcrPush + AcrPull roles (idempotent)",
        "              ACR_ID=$(az acr show --name \"$(ACR_NAME)\" --resource-group \"$(RESOURCE_GROUP)\" --query id -o tsv)",
        "              SP_OID=$(az ad sp show --id \"$(AZURE_CLIENT_ID)\" --query id -o tsv 2>/dev/null || echo '')",
        "              if [ -n \"$SP_OID\" ]; then",
        "                az role assignment create --assignee-object-id \"$SP_OID\" \\",
        "                  --assignee-principal-type ServicePrincipal \\",
        "                  --role AcrPush --scope \"$ACR_ID\" --output none 2>/dev/null || true",
        "                az role assignment create --assignee-object-id \"$SP_OID\" \\",
        "                  --assignee-principal-type ServicePrincipal \\",
        "                  --role AcrPull --scope \"$ACR_ID\" --output none 2>/dev/null || true",
        "                echo \"✓ ACR roles confirmed for Service Principal\"",
        "              fi",
        "              az acr login --name \"$(ACR_NAME)\"",
        f"              docker build -t $(ACR_NAME).azurecr.io/{image_name}:$(IMAGE_TAG) .",
        f"              docker tag  $(ACR_NAME).azurecr.io/{image_name}:$(IMAGE_TAG) \\",
        f"                          $(ACR_NAME).azurecr.io/{image_name}:latest",
        f"              docker push $(ACR_NAME).azurecr.io/{image_name}:$(IMAGE_TAG)",
        f"              docker push $(ACR_NAME).azurecr.io/{image_name}:latest",
        f"              echo \"✓ Pushed $(ACR_NAME).azurecr.io/{image_name}:$(IMAGE_TAG)\"",
        "            displayName: 'Build and push Docker image to ACR'",
    ]

    # ── Stage 4: Deploy to ACA ─────────────────────────────────────────────────
    lines += [
        "",
        "  # ── Stage 4: Deploy to Azure Container Apps ─────────────────────────",
        "  - stage: Deploy",
        "    displayName: Deploy to Azure Container Apps",
        "    dependsOn: DockerBuild",
        "    condition: succeeded()",
        "    jobs:",
        "      - deployment: DeployJob",
        "        displayName: Deploy container app",
        "        pool:",
        "          vmImage: ubuntu-latest",
        "        environment: production",
        "        strategy:",
        "          runOnce:",
        "            deploy:",
        "              steps:",
        "",
        "                - script: |",
        "                    set -euo pipefail",
        "                    az login --service-principal \\",
        "                      --username  \"$(AZURE_CLIENT_ID)\" \\",
        "                      --password  \"$(AZURE_CLIENT_SECRET)\" \\",
        "                      --tenant    \"$(AZURE_TENANT_ID)\"",
        "                    az account set --subscription \"$(AZURE_SUBSCRIPTION_ID)\"",
        "                  displayName: 'Azure login (Service Principal)'",
        "",
        "                - script: |",
        "                    set -euo pipefail",
        "                    az extension add --name containerapp --upgrade --only-show-errors 2>/dev/null || true",
        "",
        "                    # Use Service Principal for registry auth (admin credentials not required)",
        "                    # --no-wait prevents the pipeline hanging if the container takes time to start",
        "                    if az containerapp show --name \"$(APP_NAME)\" \\",
        "                         --resource-group \"$(RESOURCE_GROUP)\" &>/dev/null; then",
        f"                      az containerapp update \\",
        "                        --name           \"$(APP_NAME)\" \\",
        "                        --resource-group \"$(RESOURCE_GROUP)\" \\",
        f"                        --image          \"$(ACR_NAME).azurecr.io/{image_name}:$(IMAGE_TAG)\" \\",
        "                        --no-wait",
        "                      echo \"✓ Update triggered for container app\"",
        "                    else",
        "                      az containerapp create \\",
        "                        --name                  \"$(APP_NAME)\" \\",
        "                        --resource-group        \"$(RESOURCE_GROUP)\" \\",
        "                        --environment           \"$(ACA_ENV)\" \\",
        f"                        --image                 \"$(ACR_NAME).azurecr.io/{image_name}:$(IMAGE_TAG)\" \\",
        "                        --registry-server       \"$(ACR_NAME).azurecr.io\" \\",
        "                        --registry-username     \"$(AZURE_CLIENT_ID)\" \\",
        "                        --registry-password     \"$(AZURE_CLIENT_SECRET)\" \\",
        "                        --target-port           8080 \\",
        "                        --ingress               external \\",
        "                        --min-replicas          1 \\",
        "                        --max-replicas          3 \\",
        "                        --cpu                   0.5 \\",
        "                        --memory                1.0Gi \\",
        "                        --no-wait",
        "                      echo \"✓ Container app creation triggered\"",
        "                    fi",
        "",
        "                    echo \"Waiting 30s for deployment to initialise...\"",
        "                    sleep 30",
        "                    FQDN=$(az containerapp show \\",
        "                      --name \"$(APP_NAME)\" \\",
        "                      --resource-group \"$(RESOURCE_GROUP)\" \\",
        "                      --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || echo '')",
        "                    if [ -n \"$FQDN\" ]; then",
        "                      echo \"✓ App URL: https://$FQDN\"",
        "                      echo \"##vso[task.setvariable variable=APP_URL]https://$FQDN\"",
        "                    else",
        "                      echo \"App is starting — check Azure Portal for status\"",
        "                    fi",
        "                  displayName: 'Deploy to Azure Container Apps'",
        "",
    ]

    return "\n".join(lines) + "\n"

