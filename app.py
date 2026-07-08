"""
Flask Web UI for CI/CD Orchestrator Agent
Multi-user, multi-language, with pipeline export and run history.
"""
import os
import secrets
from datetime import datetime
from pathlib import Path

# Load .env file if present (python-dotenv, optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, jsonify, render_template, request, session

from cicd_orchestrator.database import get_db
from cicd_orchestrator.orchestrator import configure_logger
from cicd_orchestrator.pipeline import PipelineOptions, build_pipeline
from cicd_orchestrator.pipeline_exporter import EXPORTERS, export_pipeline
from cicd_orchestrator.project_detector import detect_project_info, detect_project_type
from cicd_orchestrator.workflow import Workflow

app = Flask(__name__)
# Use env var in production; fall back to a generated secret for dev
app.secret_key = os.environ.get("CICD_SECRET_KEY", secrets.token_hex(32))

logger = configure_logger()


# ── helpers ─────────────────────────────────────────────────────────────────

def _ado_defaults() -> tuple:
    """Return (org_url, project, pat) from environment variables."""
    return (
        os.environ.get("AZURE_DEVOPS_URL", "").strip(),
        os.environ.get("AZURE_DEVOPS_PROJECT", "").strip(),
        os.environ.get("AZURE_DEVOPS_PAT", "").strip(),
    )


def _sp_defaults() -> dict:
    """Return Service Principal credentials from environment variables."""
    return {
        "AZURE_CLIENT_ID":       os.environ.get("AZURE_CLIENT_ID", "").strip(),
        "AZURE_CLIENT_SECRET":   os.environ.get("AZURE_CLIENT_SECRET", "").strip(),
        "AZURE_TENANT_ID":       os.environ.get("AZURE_TENANT_ID", "").strip(),
        "AZURE_SUBSCRIPTION_ID": os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip(),
    }


def _azure_resource_defaults() -> dict:
    """Return Azure resource defaults from environment variables."""
    return {
        "subscription_id": os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip(),
        "resource_group":  os.environ.get("AZURE_RESOURCE_GROUP", "").strip(),
        "acr_name":        os.environ.get("AZURE_ACR_NAME", "").strip(),
        "app_name":        os.environ.get("AZURE_APP_NAME", "").strip(),
        "location":        os.environ.get("AZURE_LOCATION", "eastus").strip(),
        "aca_env_name":    os.environ.get("AZURE_ACA_ENV_NAME", "").strip(),
    }


def _user_id() -> str:
    """Return a stable per-browser session identifier."""
    if "user_id" not in session:
        session["user_id"] = secrets.token_hex(8)
    return session["user_id"]


def _options_from_request(data: dict) -> PipelineOptions:
    return PipelineOptions(
        include_tests=data.get("include_tests", True),
        include_lint=data.get("include_lint", False),
        cleanup=data.get("cleanup", False),
        extra_commands=data.get("extra_commands", []),
        retry=data.get("retries"),
        deploy_env=data.get("deploy_env"),
        deploy_type=data.get("deploy_type"),
        docker_image=data.get("docker_image"),
        docker_registry=data.get("docker_registry"),
        deploy_script=data.get("deploy_script"),
        k8s_manifest=data.get("k8s_manifest"),
    )


# ── UI routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── API: project detection ───────────────────────────────────────────────────

@app.route("/api/detect", methods=["POST"])
def detect_project():
    try:
        data = request.json or {}
        repo_path = data.get("path", ".")
        path_obj = Path(repo_path).resolve()
        if not path_obj.exists():
            return jsonify({"error": f"Path does not exist: {repo_path}"}), 400
        if not path_obj.is_dir():
            return jsonify({"error": f"Path is not a directory: {repo_path}"}), 400

        info = detect_project_info(path_obj)
        return jsonify({"success": True, **info})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/validate-path", methods=["POST"])
def validate_path():
    try:
        data = request.json or {}
        path_obj = Path(data.get("path", ".")).resolve()
        return jsonify({
            "exists": path_obj.exists(),
            "is_directory": path_obj.is_dir() if path_obj.exists() else False,
            "absolute_path": str(path_obj),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: pipeline preview ────────────────────────────────────────────────────

@app.route("/api/pipeline", methods=["POST"])
def get_pipeline():
    try:
        data = request.json or {}
        repo_path = data.get("path", ".")
        project_type = data.get("project_type") or detect_project_type(Path(repo_path).resolve())

        options = _options_from_request(data)
        pipeline = build_pipeline(project_type, str(Path(repo_path).resolve()), options)

        def _serialize(step):
            return {
                "name": step.name,
                "command": step.command,
                "retry": step.retry,
                "stage": step.stage.value,
                "allow_failure": step.allow_failure,
            }

        return jsonify({
            "success": True,
            "project_type": project_type,
            "steps": [_serialize(s) for s in pipeline.steps],
            "cleanup_steps": [_serialize(s) for s in pipeline.cleanup_steps],
            "build_count": len(pipeline.build_steps),
            "test_count": len(pipeline.test_steps),
            "deploy_count": len(pipeline.deploy_steps),
            "total_steps": len(pipeline.steps),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: pipeline execution ──────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def run_pipeline():
    try:
        data = request.json or {}
        repo_path = data.get("path", ".")
        project_type = data.get("project_type") or detect_project_type(Path(repo_path).resolve())
        path_obj = Path(repo_path).resolve()

        options = _options_from_request(data)
        pipeline = build_pipeline(project_type, str(path_obj), options)
        workflow = Workflow(name=f"{project_type} workflow", pipeline=pipeline)
        results, run_id = workflow.execute(
            cwd=str(path_obj),
            logger=logger,
            user_id=_user_id(),
        )

        execution_results = [
            {
                "step_name": r.step.name,
                "command": r.step.command,
                "stage": r.step.stage.value,
                "success": r.success,
                "allow_failure": r.step.allow_failure,
                "attempts": r.attempts,
                "output": (r.output or "")[:10000],
            }
            for r in results
        ]

        passed = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success and not r.step.allow_failure)
        warned = sum(1 for r in results if not r.success and r.step.allow_failure)

        return jsonify({
            "success": True,
            "run_id": run_id,
            "results": execution_results,
            "passed": passed,
            "failed": failed,
            "warned": warned,
            "total": len(results),
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: export ──────────────────────────────────────────────────────────────

@app.route("/api/export", methods=["POST"])
def export_pipeline_route():
    """Generate CI config YAML / Jenkinsfile for the given project."""
    try:
        from cicd_orchestrator.pipeline_exporter import (
            export_azure_devops_basic, export_azure_devops_aca_sp
        )
        data = request.json or {}
        repo_path = data.get("path", ".")
        path_obj  = Path(repo_path).resolve()
        project_type = data.get("project_type") or detect_project_type(path_obj)
        target = data.get("target", "github-actions")

        ADO_TARGETS = ("azure-devops-basic", "azure-devops-aca")
        if target not in EXPORTERS and target not in ADO_TARGETS:
            return jsonify({"error": f"Unknown target '{target}'."}), 400

        options  = _options_from_request(data)
        pipeline = build_pipeline(project_type, str(path_obj), options)

        if target == "azure-devops-basic":
            content  = export_azure_devops_basic(pipeline)
            filename = "azure-pipelines.yml"
        elif target == "azure-devops-aca":
            az = _azure_resource_defaults()
            content  = export_azure_devops_aca_sp(
                pipeline,
                resource_group = az["resource_group"] or "my-rg",
                acr_name       = az["acr_name"]       or "myacr",
                app_name       = az["app_name"]        or "my-app",
                location       = az["location"],
                aca_env_name   = az["aca_env_name"],
            )
            filename = "azure-pipelines-aca.yml"
        else:
            content  = export_pipeline(pipeline, target)
            filename = {
                "github-actions": ".github/workflows/pipeline.yml",
                "gitlab-ci":      ".gitlab-ci.yml",
                "jenkins":        "Jenkinsfile",
            }.get(target, "pipeline.yml")

        return jsonify({
            "success":  True,
            "target":   target,
            "filename": filename,
            "content":  content,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: run history ─────────────────────────────────────────────────────────

@app.route("/api/history", methods=["GET"])
def get_history():
    """List recent pipeline runs for the current user."""
    try:
        db = get_db()
        all_runs = request.args.get("all", "false").lower() == "true"
        user_id = None if all_runs else _user_id()
        runs = db.list_runs(user_id=user_id, limit=100)
        return jsonify({"success": True, "runs": runs, "count": len(runs)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/<run_id>", methods=["GET"])
def get_run_detail(run_id: str):
    """Get full details (including steps) for a single run."""
    try:
        db = get_db()
        run = db.get_run(run_id)
        if run is None:
            return jsonify({"error": "Run not found"}), 404
        return jsonify({"success": True, "run": run})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/<run_id>", methods=["DELETE"])
def delete_run(run_id: str):
    try:
        get_db().delete_run(run_id)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: LLM agent ───────────────────────────────────────────────────────────

@app.route("/api/pipeline/review", methods=["POST"])
def review_pipeline():
    """
    AI review of the configured pipeline steps — suggests improvements.

    Body fields
    -----------
    path          : project path
    steps         : list of pipeline step dicts (from /api/pipeline response)
    dial_api_key  : DIAL API key (overrides env var)
    dial_base_url : DIAL base URL (overrides env var)
    model         : model name (overrides env var)
    """
    try:
        from cicd_orchestrator.llm_agent import review_pipeline_with_llm
        data = request.json or {}
        path_obj = Path(data.get("path", ".")).resolve()
        steps    = data.get("steps", [])
        if not steps:
            return jsonify({"error": "No pipeline steps provided"}), 400
        result = review_pipeline_with_llm(
            project_path=str(path_obj),
            pipeline_steps=steps,
            dial_api_key=data.get("dial_api_key") or None,
            dial_base_url=data.get("dial_base_url") or None,
            model=data.get("model") or None,
        )
        return jsonify({"success": True, **result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/review", methods=["POST"])
def review_code():
    """
    AI code review powered by EPAM AI DIAL.

    Body fields
    -----------
    path            : project path (required)
    dial_api_key    : DIAL API key (overrides DIAL_API_KEY env var)
    dial_base_url   : DIAL base URL (overrides DIAL_BASE_URL env var)
    model           : model deployment name on DIAL (overrides DIAL_MODEL env var)
    token_budget    : context token budget (default: 4000)
    max_output_tokens: max tokens in LLM reply (default: 1200)
    """
    try:
        from cicd_orchestrator.code_reviewer import review_with_llm
        data = request.json or {}

        repo_path = data.get("path", ".")
        path_obj = Path(repo_path).resolve()
        if not path_obj.exists():
            return jsonify({"error": f"Path does not exist: {repo_path}"}), 400

        result = review_with_llm(
            project_path=str(path_obj),
            dial_api_key=data.get("dial_api_key") or None,
            dial_base_url=data.get("dial_base_url") or None,
            model=data.get("model") or None,
            token_budget=int(data.get("token_budget", 4000)),
            max_output_tokens=int(data.get("max_output_tokens", 1200)),
        )
        return jsonify({"success": True, **result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/llm/analyze", methods=["POST"])
def llm_analyze():
    """
    Use LLM + RAG to intelligently analyse a project and suggest a pipeline.

    Body fields
    -----------
    path              : project path (required)
    provider          : "dial" | "openai"  (default: "dial")
    model             : override model name
    api_key           : API key for the chosen provider
    dial_base_url     : override DIAL base URL
    token_budget      : context token budget (default: 3000)
    max_output_tokens : max tokens in LLM reply (default: 800)
    """
    try:
        from cicd_orchestrator.llm_agent import analyze_with_llm
        data = request.json or {}

        repo_path = data.get("path", ".")
        path_obj = Path(repo_path).resolve()
        if not path_obj.exists():
            return jsonify({"error": f"Path does not exist: {repo_path}"}), 400

        result = analyze_with_llm(
            project_path=str(path_obj),
            provider=data.get("provider", "dial"),
            model=data.get("model") or None,
            api_key=data.get("api_key") or None,
            dial_base_url=data.get("dial_base_url") or None,
            token_budget=int(data.get("token_budget", 3000)),
            max_output_tokens=int(data.get("max_output_tokens", 800)),
        )
        return jsonify({"success": True, **result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: Azure DevOps integration ────────────────────────────────────────────

@app.route("/api/azure/config", methods=["GET"])
def azure_config():
    """Return pre-configured Azure DevOps + Azure resource settings from environment."""
    env_org, env_project, env_pat = _ado_defaults()
    az = _azure_resource_defaults()
    return jsonify({
        "org_url":         env_org,
        "project":         env_project,
        "has_pat":         bool(env_pat),
        "configured":      bool(env_org and env_project and env_pat),
        "subscription_id": az["subscription_id"],
        "resource_group":  az["resource_group"],
        "acr_name":        az["acr_name"],
        "app_name":        az["app_name"],
        "location":        az["location"],
        "aca_env_name":    az["aca_env_name"],
    })


@app.route("/api/azure/repos", methods=["POST"])
def azure_list_repos():
    """
    Validate Azure DevOps credentials and return the list of git repositories.

    Body: { org_url, project, pat }  — all optional when env vars are set.
    """
    try:
        from cicd_orchestrator.azure_devops import AzureDevOpsClient
        env_org, env_project, env_pat = _ado_defaults()
        data    = request.json or {}
        org_url = data.get("org_url", "").strip() or env_org
        project = data.get("project", "").strip() or env_project
        pat     = data.get("pat", "").strip() or env_pat

        if not org_url or not project or not pat:
            return jsonify({"error": "org_url, project and pat are required (or set env vars)"}), 400

        client = AzureDevOpsClient(org_url, project, pat)
        client.validate_connection()
        repos = client.list_repositories()
        return jsonify({
            "success": True,
            "repos": [{"id": r["id"], "name": r["name"]} for r in repos],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/azure/deploy", methods=["POST"])
def azure_deploy():
    """
    Full end-to-end Azure DevOps pipeline creation + trigger.

    Steps
    -----
    1. Build pipeline object from project path
    2. Generate azure-pipelines.yml (with ACA deploy stages)
    3. Push YAML to the Azure DevOps repo
    4. Create pipeline definition in Azure DevOps
    5. Trigger first run
    6. Return pipeline URL + run URL + generated YAML

    Body fields
    -----------
    path               : local project path (for pipeline generation)
    project_type       : (optional) override detected type
    org_url            : https://dev.azure.com/myorg
    project            : Azure DevOps project name
    pat                : Personal Access Token
    repo_name          : Azure DevOps repository name
    service_connection : Azure service connection name in ADO
    acr_name           : Azure Container Registry name
    resource_group     : Azure resource group
    app_name           : Azure Container App name
    branch             : target branch (default: main)
    include_tests      : bool (default: true)
    include_lint       : bool (default: false)
    """
    try:
        from cicd_orchestrator.azure_devops import AzureDevOpsClient
        from cicd_orchestrator.pipeline_exporter import export_azure_devops_aca
        data = request.json or {}

        # ── project path + pipeline ──────────────────────────────────────────
        repo_path    = data.get("path", ".")
        path_obj     = Path(repo_path).resolve()
        project_type = data.get("project_type") or detect_project_type(path_obj)
        options      = _options_from_request(data)
        pipeline     = build_pipeline(project_type, str(path_obj), options)

        # ── Azure params ─────────────────────────────────────────────────────
        env_org, env_project, env_pat = _ado_defaults()
        org_url            = (data.get("org_url", "").strip() or env_org)
        ado_project        = (data.get("project", "").strip() or env_project)
        pat                = (data.get("pat", "").strip() or env_pat)
        repo_name          = data.get("repo_name", "").strip()
        service_connection = data.get("service_connection", "azure-service-connection").strip()
        acr_name           = data.get("acr_name", "").strip()
        resource_group     = data.get("resource_group", "").strip()
        app_name           = data.get("app_name", "").strip()
        branch             = data.get("branch", "main").strip()

        for field, val in [
            ("org_url", org_url), ("project", ado_project), ("pat", pat),
            ("repo_name", repo_name), ("acr_name", acr_name),
            ("resource_group", resource_group), ("app_name", app_name),
        ]:
            if not val:
                return jsonify({"error": f"'{field}' is required"}), 400

        # ── generate YAML ────────────────────────────────────────────────────
        yaml_content = export_azure_devops_aca(
            pipeline,
            acr_name=acr_name,
            resource_group=resource_group,
            app_name=app_name,
            service_connection=service_connection,
            branch=branch,
        )

        # ── Azure DevOps API calls ───────────────────────────────────────────
        client = AzureDevOpsClient(org_url, ado_project, pat)
        repo   = client.get_repository(repo_name)
        repo_id = repo["id"]

        # 1. Push all project source files + azure-pipelines-aca.yml in one commit
        aca_yaml_path = "/azure-pipelines-aca.yml"
        _, file_count, skipped = client.push_directory(
            repo_id=repo_id,
            local_path=str(path_obj),
            branch=branch,
            commit_message=f"Push {project_type} project + ACA pipeline [CI/CD Orchestrator Agent]",
            extra_files={aca_yaml_path: yaml_content},
        )

        # 2. Create pipeline definition — reuse existing one if already created
        aca_pipeline_name = f"{app_name} → Azure Container Apps"
        existing = client.get_pipeline_by_name(aca_pipeline_name)
        if existing:
            pipeline_id = existing["id"]
        else:
            pipeline_def = client.create_pipeline(
                name=aca_pipeline_name,
                repo_id=repo_id,
                repo_name=repo_name,
                yaml_path=aca_yaml_path,
                branch=branch,
            )
            pipeline_id = pipeline_def["id"]

        # 3. Trigger run
        run = client.run_pipeline(pipeline_id, branch=branch)
        run_id = run["id"]

        return jsonify({
            "success":       True,
            "project_type":  project_type,
            "pipeline_id":   pipeline_id,
            "run_id":        run_id,
            "files_pushed":  file_count,
            "files_skipped": len(skipped),
            "pipeline_url":  client.pipeline_web_url(pipeline_id),
            "run_url":       client.run_web_url(run_id),
            "yaml_content":  yaml_content,
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/azure/deploy-basic", methods=["POST"])
def azure_deploy_basic():
    """
    Lightweight end-to-end Azure DevOps pipeline creation + trigger (CI only).

    Steps
    -----
    1. Detect/build pipeline from local project path
    2. Generate a simple azure-pipelines.yml (build + test stages, no ACA)
    3. Push YAML to the Azure DevOps repo
    4. Create pipeline definition
    5. Trigger first run
    6. Return pipeline URL + run URL + generated YAML

    Body fields
    -----------
    path           : local project path (required)
    project_type   : (optional) override detected type
    org_url        : https://dev.azure.com/myorg  (required)
    project        : Azure DevOps project name     (required)
    pat            : Personal Access Token         (required)
    repo_name      : Azure DevOps repo name        (required)
    pipeline_name  : pipeline definition name      (default: "CI Pipeline")
    branch         : target branch                 (default: "main")
    include_tests  : bool                          (default: true)
    include_lint   : bool                          (default: false)
    """
    try:
        from cicd_orchestrator.azure_devops import AzureDevOpsClient
        from cicd_orchestrator.pipeline_exporter import export_azure_devops_basic
        data = request.json or {}

        # ── project path + pipeline ──────────────────────────────────────────
        repo_path    = data.get("path", ".")
        path_obj     = Path(repo_path).resolve()
        project_type = data.get("project_type") or detect_project_type(path_obj)
        options      = _options_from_request(data)
        pipeline     = build_pipeline(project_type, str(path_obj), options)

        # ── Azure params ─────────────────────────────────────────────────────
        env_org, env_project, env_pat = _ado_defaults()
        org_url       = (data.get("org_url", "").strip() or env_org)
        ado_project   = (data.get("project", "").strip() or env_project)
        pat           = (data.get("pat", "").strip() or env_pat)
        repo_name     = data.get("repo_name", "").strip()
        pipeline_name = data.get("pipeline_name", "CI Pipeline").strip() or "CI Pipeline"
        branch        = data.get("branch", "main").strip() or "main"

        for field, val in [
            ("org_url", org_url), ("project", ado_project),
            ("pat", pat), ("repo_name", repo_name),
        ]:
            if not val:
                return jsonify({"error": f"'{field}' is required"}), 400

        # ── generate YAML ────────────────────────────────────────────────────
        yaml_content = export_azure_devops_basic(
            pipeline,
            pipeline_name=pipeline_name,
            branch=branch,
        )

        # ── Azure DevOps API calls ───────────────────────────────────────────
        client  = AzureDevOpsClient(org_url, ado_project, pat)
        repo    = client.get_repository(repo_name)
        repo_id = repo["id"]

        # 1. Push all project source files + azure-pipelines.yml in one commit
        _, file_count, skipped = client.push_directory(
            repo_id=repo_id,
            local_path=str(path_obj),
            branch=branch,
            commit_message=f"Push {project_type} project + CI pipeline [CI/CD Orchestrator Agent]",
            extra_files={"/azure-pipelines.yml": yaml_content},
        )

        # 2. Create pipeline definition — reuse existing one if already created
        existing = client.get_pipeline_by_name(pipeline_name)
        if existing:
            pipeline_id = existing["id"]
        else:
            pipeline_def = client.create_pipeline(
                name=pipeline_name,
                repo_id=repo_id,
                repo_name=repo_name,
                yaml_path="/azure-pipelines.yml",
                branch=branch,
            )
            pipeline_id = pipeline_def["id"]

        # 3. Trigger run
        run    = client.run_pipeline(pipeline_id, branch=branch)
        run_id = run["id"]

        return jsonify({
            "success":      True,
            "project_type": project_type,
            "pipeline_id":  pipeline_id,
            "run_id":       run_id,
            "files_pushed": file_count,
            "files_skipped": len(skipped),
            "pipeline_url": client.pipeline_web_url(pipeline_id),
            "run_url":      client.run_web_url(run_id),
            "yaml_content": yaml_content,
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/azure/deploy-aca", methods=["POST"])
def azure_deploy_aca_sp():
    """
    End-to-end: push code + generate ACA pipeline (Service Principal auth) + trigger.

    Body fields
    -----------
    path              : local project path       (required)
    project_type      : override detected type   (optional)
    org_url / project / pat : ADO creds          (fall back to env vars)
    repo_name         : ADO repo name            (required)
    resource_group    : Azure RG name            (default: kanchana-rg)
    acr_name          : ACR name                 (default: ecommerceacr)
    app_name          : Container App name       (default: ecommerce-app)
    location          : Azure region             (default: eastus)
    aca_env_name      : ACA environment name     (default: <app_name>-env)
    pipeline_name     : ADO pipeline name        (default: Deploy to Azure Container Apps)
    branch            : git branch               (default: main)
    include_tests     : bool                     (default: true)
    """
    try:
        from cicd_orchestrator.azure_devops import AzureDevOpsClient
        from cicd_orchestrator.pipeline_exporter import export_azure_devops_aca_sp
        data = request.json or {}

        # ── project type + optional local path ───────────────────────────────
        repo_path    = (data.get("path") or "").strip()
        push_code    = bool(repo_path)          # push code only when path given
        path_obj     = Path(repo_path).resolve() if push_code else Path(".")
        project_type = (data.get("project_type") or "").strip()
        if not project_type:
            project_type = detect_project_type(path_obj) if push_code else "unknown"
        options  = _options_from_request(data)
        pipeline = build_pipeline(project_type, str(path_obj) if push_code else ".", options)

        # ── ADO params ───────────────────────────────────────────────────────
        env_org, env_project, env_pat = _ado_defaults()
        org_url     = data.get("org_url", "").strip() or env_org
        ado_project = data.get("project", "").strip() or env_project
        pat         = data.get("pat", "").strip() or env_pat
        repo_name   = data.get("repo_name", "").strip()

        for field, val in [("org_url", org_url), ("project", ado_project),
                           ("pat", pat), ("repo_name", repo_name)]:
            if not val:
                return jsonify({"error": f"'{field}' is required"}), 400

        # ── Azure resource params (fall back to env vars) ─────────────────────
        az = _azure_resource_defaults()
        resource_group = data.get("resource_group", "").strip() or az["resource_group"]
        acr_name       = data.get("acr_name", "").strip()       or az["acr_name"]
        app_name       = data.get("app_name", "").strip()        or az["app_name"]
        location       = data.get("location", "").strip()        or az["location"] or "eastus"
        aca_env_name   = data.get("aca_env_name", "").strip()    or az["aca_env_name"]
        pipeline_name  = data.get("pipeline_name", "Deploy to Azure Container Apps").strip()
        branch         = data.get("branch", "main").strip() or "main"

        # ── generate YAML ────────────────────────────────────────────────────
        skip_provision = bool(data.get("skip_provision", False))
        yaml_content = export_azure_devops_aca_sp(
            pipeline,
            resource_group=resource_group,
            acr_name=acr_name,
            app_name=app_name,
            location=location,
            aca_env_name=aca_env_name,
            branch=branch,
            pipeline_name=pipeline_name,
            skip_provision=skip_provision,
        )

        # ── push YAML (+ code if local path given) + create pipeline + trigger ──
        client  = AzureDevOpsClient(org_url, ado_project, pat)
        repo    = client.get_repository(repo_name)
        repo_id = repo["id"]

        aca_yaml_path = "/azure-pipelines-aca.yml"
        file_count = 0
        if push_code:
            _, file_count, _ = client.push_directory(
                repo_id=repo_id,
                local_path=str(path_obj),
                branch=branch,
                commit_message=f"Push {project_type} project + ACA pipeline [CI/CD Orchestrator Agent]",
                extra_files={aca_yaml_path: yaml_content},
            )
        else:
            client.push_file(
                repo_id=repo_id,
                file_path=aca_yaml_path,
                content=yaml_content,
                branch=branch,
                commit_message="Update azure-pipelines-aca.yml [CI/CD Orchestrator Agent]",
            )

        existing = client.get_pipeline_by_name(pipeline_name)
        if existing:
            pipeline_id = existing["id"]
        else:
            pipeline_def = client.create_pipeline(
                name=pipeline_name,
                repo_id=repo_id,
                repo_name=repo_name,
                yaml_path=aca_yaml_path,
                branch=branch,
            )
            pipeline_id = pipeline_def["id"]

        # Auto-set SP secret variables on the pipeline — no manual steps needed
        sp = _sp_defaults()
        # Allow per-request overrides
        sp_vars = {
            "AZURE_CLIENT_ID":       data.get("azure_client_id", "").strip()       or sp["AZURE_CLIENT_ID"],
            "AZURE_CLIENT_SECRET":   data.get("azure_client_secret", "").strip()   or sp["AZURE_CLIENT_SECRET"],
            "AZURE_TENANT_ID":       data.get("azure_tenant_id", "").strip()       or sp["AZURE_TENANT_ID"],
            "AZURE_SUBSCRIPTION_ID": data.get("azure_subscription_id", "").strip() or sp["AZURE_SUBSCRIPTION_ID"],
        }
        vars_set = False
        vars_error = None
        if all(sp_vars.values()):
            try:
                client.set_pipeline_variables(pipeline_id, sp_vars)
                vars_set = True
            except Exception as ve:
                vars_error = str(ve)

        run    = client.run_pipeline(pipeline_id, branch=branch)
        run_id = run["id"]

        return jsonify({
            "success":       True,
            "project_type":  project_type,
            "pipeline_id":   pipeline_id,
            "run_id":        run_id,
            "files_pushed":  file_count,
            "vars_set":      vars_set,
            "pipeline_url":  client.pipeline_web_url(pipeline_id),
            "run_url":       client.run_web_url(run_id),
            "yaml_content":  yaml_content,
            "next_step":     None if vars_set else (
                f"SP variables could not be set automatically ({vars_error}). "
                "Add manually in ADO → Pipelines → Variables: "
                "AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID"
            ),
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: meta ────────────────────────────────────────────────────────────────

@app.route("/api/supported-types", methods=["GET"])
def supported_types():
    from cicd_orchestrator.project_detector import SUPPORTED_TYPES
    return jsonify({"types": SUPPORTED_TYPES})


@app.route("/api/export-targets", methods=["GET"])
def export_targets():
    return jsonify({"targets": list(EXPORTERS.keys())})


@app.route("/api/dial/config", methods=["GET"])
def dial_config():
    """Return DIAL configuration status from environment — never exposes the key itself."""
    return jsonify({
        "has_key":  bool(os.environ.get("DIAL_API_KEY", "").strip()),
        "base_url": os.environ.get("DIAL_BASE_URL", "https://ai-proxy.lab.epam.com").strip(),
        "model":    os.environ.get("DIAL_MODEL", "gpt-4o").strip(),
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print("\n" + "=" * 70)
    print("  CI/CD Orchestrator Agent - Web UI")
    print("=" * 70)
    print(f"\n  >>  Running at: http://localhost:{port}")
    print("  Press Ctrl+C to stop.\n")
    app.run(debug=debug, host="0.0.0.0", port=port)
