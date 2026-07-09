"""
CI/CD Orchestrator MCP Server

Exposes the orchestrator's capabilities as MCP tools so any MCP-compatible
AI client (Claude Code, GitHub Copilot, Cursor, etc.) can:
  • detect a project's language + test/Docker setup
  • preview and run pipelines locally
  • export CI config for GitHub Actions / GitLab / Jenkins
  • deploy to Azure DevOps (push code + create pipeline + set SP vars + trigger)
  • query run history
  • AI code review (security/bug/perf findings via EPAM AI DIAL)
  • AI pipeline review (suggests pipeline improvements via EPAM AI DIAL)

Usage
-----
  python mcp_server.py          # stdio mode (for mcp.json / Claude Desktop)
  python mcp_server.py --http   # HTTP SSE mode on port 5001

  # Or run directly from the GitHub repo with no local clone (like `npx`):
  uvx --from git+https://github.com/kanchanachintala96/CICD.git cicd-orchestrator-mcp

Config (from .env or environment)
----------------------------------
  AZURE_DEVOPS_URL, AZURE_DEVOPS_PROJECT, AZURE_DEVOPS_PAT
  AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_ACR_NAME
  AZURE_APP_NAME, AZURE_LOCATION, AZURE_ACA_ENV_NAME
  AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID
  When run via uvx/pip install (no local .env file available), pass these as
  the "env" block in mcp.json instead — see README for an example.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Load .env if present at the repo root (when run from a local clone).
# No-op when run via uvx/pip from GitHub with no .env alongside it —
# credentials then come from mcp.json's "env" block instead.
try:
    from dotenv import load_dotenv
    _repo_root_env = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(_repo_root_env)
except ImportError:
    pass

from mcp.server.fastmcp import FastMCP

from .project_detector import detect_project_info, detect_project_type
from .pipeline import PipelineOptions, build_pipeline
from .pipeline_exporter import (
    EXPORTERS,
    export_azure_devops_aca_sp,
    export_pipeline,
)
from .orchestrator import configure_logger
from .workflow import Workflow
from cicd_orchestrator.database import get_db

mcp = FastMCP(
    "cicd-orchestrator",
    instructions=(
        "CI/CD Orchestrator Agent — detects project types, generates pipelines, "
        "runs them locally, and deploys to Azure DevOps with one call."
    ),
)

logger = configure_logger()


# ── helpers ──────────────────────────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _ado_creds() -> tuple[str, str, str]:
    return _env("AZURE_DEVOPS_URL"), _env("AZURE_DEVOPS_PROJECT"), _env("AZURE_DEVOPS_PAT")


def _sp_creds() -> dict:
    return {
        "AZURE_CLIENT_ID":       _env("AZURE_CLIENT_ID"),
        "AZURE_CLIENT_SECRET":   _env("AZURE_CLIENT_SECRET"),
        "AZURE_TENANT_ID":       _env("AZURE_TENANT_ID"),
        "AZURE_SUBSCRIPTION_ID": _env("AZURE_SUBSCRIPTION_ID"),
    }


def _github_creds() -> tuple[str, str, str]:
    return _env("GITHUB_TOKEN"), _env("GITHUB_OWNER"), _env("GITHUB_REPO")


def _az_resources() -> dict:
    return {
        "resource_group": _env("AZURE_RESOURCE_GROUP"),
        "acr_name":       _env("AZURE_ACR_NAME"),
        "app_name":       _env("AZURE_APP_NAME"),
        "location":       _env("AZURE_LOCATION", "eastus"),
        "aca_env_name":   _env("AZURE_ACA_ENV_NAME"),
    }


# ── Tool 1: detect_project ────────────────────────────────────────────────────

@mcp.tool()
def detect_project(path: str) -> dict:
    """
    Detect a project's language, framework, and setup from a local directory path.

    Returns project type (python/nodejs/java-maven/java-gradle/go/dotnet/ruby/docker),
    whether it has tests, a Dockerfile, lint config, and for Node.js the npm scripts.

    Args:
        path: Absolute or relative path to the project folder.
    """
    p = Path(path).resolve()
    if not p.exists() or not p.is_dir():
        return {"error": f"Path does not exist or is not a directory: {path}"}
    return detect_project_info(p)


# ── Tool 2: preview_pipeline ─────────────────────────────────────────────────

@mcp.tool()
def preview_pipeline(
    path: str,
    project_type: str = "",
    include_tests: bool = True,
    include_lint: bool = False,
    extra_commands: list[str] | None = None,
) -> dict:
    """
    Generate and preview the pipeline steps for a project without running anything.

    Returns a list of build/test/deploy steps with their commands and stages.

    Args:
        path:             Local project folder path.
        project_type:     Override auto-detected type (e.g. 'java-maven', 'python').
        include_tests:    Include test steps (default: True).
        include_lint:     Include lint steps (default: False).
        extra_commands:   Extra shell commands to append to the pipeline.
    """
    p = Path(path).resolve()
    ptype = project_type or detect_project_type(p)
    opts = PipelineOptions(
        include_tests=include_tests,
        include_lint=include_lint,
        extra_commands=extra_commands or [],
    )
    pipeline = build_pipeline(ptype, str(p), opts)
    return {
        "project_type": ptype,
        "steps": [
            {"name": s.name, "command": s.command, "stage": s.stage.value}
            for s in pipeline.steps
        ],
        "total_steps": len(pipeline.steps),
        "build_count":  len(pipeline.build_steps),
        "test_count":   len(pipeline.test_steps),
        "deploy_count": len(pipeline.deploy_steps),
    }


# ── Tool 3: run_pipeline_locally ─────────────────────────────────────────────

@mcp.tool()
def run_pipeline_locally(
    path: str,
    project_type: str = "",
    include_tests: bool = True,
    include_lint: bool = False,
    extra_commands: list[str] | None = None,
) -> dict:
    """
    Execute pipeline steps locally (build + test) in the given project directory.

    Returns step-by-step results including pass/fail status and command output.
    Use this to validate the pipeline before deploying to Azure DevOps.

    Args:
        path:           Local project folder path.
        project_type:   Override auto-detected type.
        include_tests:  Run test steps (default: True).
        include_lint:   Run lint steps (default: False).
        extra_commands: Extra shell commands to include.
    """
    p = Path(path).resolve()
    if not p.exists():
        return {"error": f"Path does not exist: {path}"}

    ptype = project_type or detect_project_type(p)
    opts = PipelineOptions(
        include_tests=include_tests,
        include_lint=include_lint,
        extra_commands=extra_commands or [],
    )
    pipeline = build_pipeline(ptype, str(p), opts)
    workflow = Workflow(name=f"{ptype} pipeline", pipeline=pipeline)
    results, run_id = workflow.execute(cwd=str(p), logger=logger)

    steps_out = [
        {
            "name":    r.step.name,
            "stage":   r.step.stage.value,
            "command": r.step.command,
            "success": r.success,
            "output":  (r.output or "")[:2000],
        }
        for r in results
    ]
    passed = sum(1 for r in results if r.success)
    return {
        "run_id":  run_id,
        "passed":  passed,
        "failed":  len(results) - passed,
        "total":   len(results),
        "steps":   steps_out,
    }


# ── Tool 4: export_pipeline_yaml ─────────────────────────────────────────────

@mcp.tool()
def export_pipeline_yaml(
    path: str,
    target: str = "github-actions",
    project_type: str = "",
    include_tests: bool = True,
    extra_commands: list[str] | None = None,
) -> str:
    """
    Export a ready-to-commit CI/CD config file for the given project.

    Supported targets: github-actions, gitlab-ci, jenkins, azure-devops-basic, azure-devops-aca.

    Pass `extra_commands` to bake in steps suggested by `review_pipeline` (or any
    other commands) so exported YAML already includes them — e.g. after calling
    review_pipeline and getting a suggestion's `command`, pass it here to apply it.

    Args:
        path:            Local project path (used for type detection).
        target:          Export format — 'github-actions' | 'gitlab-ci' | 'jenkins' |
                         'azure-devops-basic' | 'azure-devops-aca'
        project_type:    Override auto-detected type.
        include_tests:   Include test stages (default: True).
        extra_commands:  Extra shell commands to append to the pipeline (e.g. from
                         review_pipeline suggestions the user wants to apply).
    """
    p = Path(path).resolve() if path else Path(".")
    ptype = project_type or detect_project_type(p)
    opts = PipelineOptions(include_tests=include_tests, extra_commands=extra_commands or [])
    pipeline = build_pipeline(ptype, str(p), opts)

    if target in EXPORTERS:
        return export_pipeline(pipeline, target)

    if target == "azure-devops-basic":
        from cicd_orchestrator.pipeline_exporter import export_azure_devops_basic
        return export_azure_devops_basic(pipeline)

    if target == "azure-devops-aca":
        az = _az_resources()
        return export_azure_devops_aca_sp(
            pipeline,
            resource_group=az["resource_group"] or "my-rg",
            acr_name=az["acr_name"] or "myacr",
            app_name=az["app_name"] or "my-app",
            location=az["location"],
            aca_env_name=az["aca_env_name"],
        )

    return f"Unknown target '{target}'. Choose from: {list(EXPORTERS)} + azure-devops-basic + azure-devops-aca"


# ── Tool 5: deploy_to_azure_devops ───────────────────────────────────────────

@mcp.tool()
def deploy_to_azure_devops(
    repo_name: str,
    project_type: str = "java-maven",
    path: str = "",
    branch: str = "main",
    pipeline_name: str = "Deploy to Azure Container Apps",
    skip_provision: bool = True,
    include_tests: bool = True,
    resource_group: str = "",
    acr_name: str = "",
    app_name: str = "",
    location: str = "",
    extra_commands: list[str] | None = None,
) -> dict:
    """
    Full end-to-end deploy: push code to Azure Repos, create ADO pipeline,
    set Service Principal secret variables automatically, and trigger the first run.

    Credentials (AZURE_DEVOPS_PAT, AZURE_CLIENT_ID, etc.) are read from .env.
    All Azure resource defaults are read from .env too — only override if needed.

    Pass `extra_commands` to bake in steps suggested by `review_pipeline` (or any
    other commands) into the deployed pipeline.

    Args:
        repo_name:      Azure DevOps repository name to push to.
        project_type:   Project language type (java-maven/python/nodejs/go/dotnet/docker).
        path:           Local project path. If blank, only pushes the pipeline YAML.
        branch:         Target branch (default: main).
        pipeline_name:  Name for the ADO pipeline definition.
        skip_provision: Skip Azure infra provisioning (use when RG/ACR/ACA already exist).
        include_tests:  Include test stage in pipeline.
        resource_group: Override AZURE_RESOURCE_GROUP from .env.
        acr_name:       Override AZURE_ACR_NAME from .env.
        app_name:       Override AZURE_APP_NAME from .env.
        location:       Override AZURE_LOCATION from .env.
        extra_commands: Extra shell commands to append to the pipeline (e.g. from
                        review_pipeline suggestions the user wants to apply).
    """
    from cicd_orchestrator.azure_devops import AzureDevOpsClient

    org_url, ado_project, pat = _ado_creds()
    if not all([org_url, ado_project, pat]):
        return {"error": "ADO credentials missing. Set AZURE_DEVOPS_URL, AZURE_DEVOPS_PROJECT, AZURE_DEVOPS_PAT in .env"}

    az = _az_resources()
    rg       = resource_group or az["resource_group"]
    acr      = acr_name       or az["acr_name"]
    app      = app_name       or az["app_name"]
    loc      = location       or az["location"]
    aca_env  = az["aca_env_name"]

    if not all([rg, acr, app]):
        return {"error": "Azure resource details missing. Set AZURE_RESOURCE_GROUP, AZURE_ACR_NAME, AZURE_APP_NAME in .env"}

    push_code = bool(path)
    path_obj  = Path(path).resolve() if push_code else Path(".")
    opts      = PipelineOptions(include_tests=include_tests, extra_commands=extra_commands or [])
    pipeline  = build_pipeline(project_type, str(path_obj) if push_code else ".", opts)

    yaml_content = export_azure_devops_aca_sp(
        pipeline,
        resource_group=rg,
        acr_name=acr,
        app_name=app,
        location=loc,
        aca_env_name=aca_env,
        branch=branch,
        pipeline_name=pipeline_name,
        skip_provision=skip_provision,
    )

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
            commit_message=f"Push {project_type} project + ACA pipeline [CI/CD Orchestrator MCP]",
            extra_files={aca_yaml_path: yaml_content},
        )
    else:
        client.push_file(
            repo_id=repo_id,
            file_path=aca_yaml_path,
            content=yaml_content,
            branch=branch,
            commit_message="Update azure-pipelines-aca.yml [CI/CD Orchestrator MCP]",
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

    # Auto-set SP secret variables
    sp = _sp_creds()
    vars_set = False
    if all(sp.values()):
        try:
            client.set_pipeline_variables(pipeline_id, sp)
            vars_set = True
        except Exception:
            pass

    run    = client.run_pipeline(pipeline_id, branch=branch)
    run_id = run["id"]

    return {
        "success":      True,
        "project_type": project_type,
        "pipeline_id":  pipeline_id,
        "run_id":       run_id,
        "files_pushed": file_count,
        "vars_set":     vars_set,
        "pipeline_url": client.pipeline_web_url(pipeline_id),
        "run_url":      client.run_web_url(run_id),
    }


# ── Tool 6: get_run_history ───────────────────────────────────────────────────

@mcp.tool()
def get_run_history(limit: int = 10) -> list:
    """
    Return recent local pipeline run history (all users).

    Args:
        limit: Maximum number of runs to return (default: 10).
    """
    db = get_db()
    runs = db.list_runs(user_id=None, limit=limit)
    return runs


# ── Tool 7: list_ado_repos ────────────────────────────────────────────────────

@mcp.tool()
def list_ado_repos() -> list:
    """
    List all Azure DevOps repositories in the configured project.
    Uses credentials from .env (AZURE_DEVOPS_URL / PROJECT / PAT).
    """
    from cicd_orchestrator.azure_devops import AzureDevOpsClient
    org_url, project, pat = _ado_creds()
    if not all([org_url, project, pat]):
        return [{"error": "ADO credentials not configured in .env"}]
    client = AzureDevOpsClient(org_url, project, pat)
    repos  = client.list_repositories()
    return [{"id": r["id"], "name": r["name"]} for r in repos]


# ── Tool 8: review_code ───────────────────────────────────────────────────────

@mcp.tool()
def review_code(
    path: str,
    dial_api_key: str = "",
    dial_base_url: str = "",
    model: str = "",
    token_budget: int = 4000,
    max_output_tokens: int = 1200,
) -> dict:
    """
    AI-powered code review of a project, powered by EPAM AI DIAL.

    Reads the project's source files via RAG and asks an LLM to review them for
    security issues, bugs, performance, and maintainability. Returns an overall
    rating, a summary, positive aspects, and a prioritised list of findings
    (each with severity, category, message, and an actionable suggestion).

    Call this whenever the user asks to "review my code", "check for security
    issues", "audit this project", etc.

    Args:
        path:              Local project folder path.
        dial_api_key:       DIAL API key (falls back to DIAL_API_KEY in .env).
        dial_base_url:      DIAL base URL (falls back to DIAL_BASE_URL in .env).
        model:              DIAL model deployment name (falls back to DIAL_MODEL in .env).
        token_budget:       Context token budget for the RAG pass (default: 4000).
        max_output_tokens:  Max tokens in the LLM's reply (default: 1200).
    """
    from cicd_orchestrator.code_reviewer import review_with_llm
    p = Path(path).resolve()
    if not p.exists():
        return {"error": f"Path does not exist: {path}"}
    return review_with_llm(
        project_path=str(p),
        dial_api_key=dial_api_key or None,
        dial_base_url=dial_base_url or None,
        model=model or None,
        token_budget=token_budget,
        max_output_tokens=max_output_tokens,
    )


# ── Tool 9: review_pipeline ───────────────────────────────────────────────────

@mcp.tool()
def review_pipeline(
    path: str,
    project_type: str = "",
    include_tests: bool = True,
    include_lint: bool = False,
    extra_commands: list[str] | None = None,
    dial_api_key: str = "",
    dial_base_url: str = "",
    model: str = "",
) -> dict:
    """
    AI review of a project's generated CI/CD pipeline — suggests concrete
    improvements (e.g. security scans, caching, coverage gates, missing steps)
    with a priority, category, description, and a ready-to-use shell command
    for each suggestion.

    Builds the pipeline for `path` internally (same as preview_pipeline), so it
    can be called standalone with just a path — no need to call preview_pipeline
    first. Call this when the user asks to "review my pipeline", "suggest
    pipeline improvements", "how can I make my CI/CD better", etc.

    Args:
        path:             Local project folder path.
        project_type:     Override auto-detected type (e.g. 'java-maven', 'python').
        include_tests:    Include test steps when building the pipeline (default: True).
        include_lint:     Include lint steps when building the pipeline (default: False).
        extra_commands:   Extra shell commands already added to the pipeline.
        dial_api_key:     DIAL API key (falls back to DIAL_API_KEY in .env).
        dial_base_url:    DIAL base URL (falls back to DIAL_BASE_URL in .env).
        model:            DIAL model deployment name (falls back to DIAL_MODEL in .env).
    """
    from cicd_orchestrator.llm_agent import review_pipeline_with_llm
    p = Path(path).resolve()
    if not p.exists():
        return {"error": f"Path does not exist: {path}"}
    ptype = project_type or detect_project_type(p)
    opts = PipelineOptions(
        include_tests=include_tests,
        include_lint=include_lint,
        extra_commands=extra_commands or [],
    )
    pipeline = build_pipeline(ptype, str(p), opts)
    steps = [
        {"name": s.name, "command": s.command, "stage": s.stage.value}
        for s in pipeline.steps
    ]
    return review_pipeline_with_llm(
        project_path=str(p),
        pipeline_steps=steps,
        dial_api_key=dial_api_key or None,
        dial_base_url=dial_base_url or None,
        model=model or None,
    )


# ── Tool 10: push_to_github_and_trigger_ado ──────────────────────────────────

@mcp.tool()
def push_to_github_and_trigger_ado(
    ado_pipeline_name: str,
    path: str = "",
    github_owner: str = "",
    github_repo: str = "",
    github_token: str = "",
    branch: str = "main",
    push_code: bool = True,
    set_secret: bool = True,
) -> dict:
    """
    Push project code to a GitHub repository and wire up a GitHub Actions
    workflow that triggers an *existing* Azure DevOps pipeline run on every
    push — so GitHub hosts the code/trigger while ADO remains the execution
    engine (build/test/deploy).

    Requires the ADO pipeline to already exist (e.g. created earlier via
    deploy_to_azure_devops). Azure DevOps credentials are read from .env
    (AZURE_DEVOPS_URL / AZURE_DEVOPS_PROJECT / AZURE_DEVOPS_PAT).

    Call this when the user asks to "push to GitHub and trigger my ADO
    pipeline from GitHub Actions", "set up GitHub Actions to kick off my
    Azure DevOps pipeline", etc.

    Args:
        ado_pipeline_name: Name of the existing Azure DevOps pipeline to trigger.
        path:              Local project path to push. If blank, only the
                            trigger workflow file is pushed (no source code).
        github_owner:      GitHub org/user (falls back to GITHUB_OWNER in .env).
        github_repo:        GitHub repo name (falls back to GITHUB_REPO in .env).
        github_token:      GitHub PAT with repo+workflow scope (falls back to
                            GITHUB_TOKEN in .env).
        branch:            Branch to push to and trigger on (default: main).
        push_code:         Whether to push the local project's source files too
                            (default: True). Set False to only add the workflow.
        set_secret:        Attempt to auto-create the ADO_PAT GitHub Actions
                            secret from the ADO PAT in .env (requires PyNaCl).
                            If it fails, add it manually in GitHub → Settings →
                            Secrets and variables → Actions.
    """
    from cicd_orchestrator.github_client import GitHubClient
    from cicd_orchestrator.azure_devops import AzureDevOpsClient
    from cicd_orchestrator.pipeline_exporter import export_github_actions_trigger_ado

    org_url, ado_project, ado_pat = _ado_creds()
    if not all([org_url, ado_project, ado_pat]):
        return {"error": "ADO credentials missing. Set AZURE_DEVOPS_URL, AZURE_DEVOPS_PROJECT, AZURE_DEVOPS_PAT in .env"}

    gh_token = github_token or _env("GITHUB_TOKEN")
    gh_owner = github_owner or _env("GITHUB_OWNER")
    gh_repo  = github_repo or _env("GITHUB_REPO")
    if not all([gh_token, gh_owner, gh_repo]):
        return {"error": "GitHub credentials missing. Pass github_owner/github_repo/github_token or set GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO in .env"}

    ado_client = AzureDevOpsClient(org_url, ado_project, ado_pat)
    pipeline_def = ado_client.get_pipeline_by_name(ado_pipeline_name)
    if not pipeline_def:
        return {"error": f"Azure DevOps pipeline '{ado_pipeline_name}' not found in project '{ado_project}'."}
    pipeline_id = pipeline_def["id"]

    gh_client = GitHubClient(gh_token, gh_owner, gh_repo)
    gh_client.validate_connection()

    workflow_yaml = export_github_actions_trigger_ado(
        org_url=org_url, project=ado_project, pipeline_id=pipeline_id, branch=branch,
    )

    pushed_files = 0
    skipped: list[str] = []
    if push_code and path:
        p = Path(path).resolve()
        if not p.exists():
            return {"error": f"Path does not exist: {path}"}
        pushed_files, skipped = gh_client.push_directory(
            str(p), branch=branch,
            commit_message="Push project files [CI/CD Orchestrator Agent]",
            extra_files={".github/workflows/trigger-ado-pipeline.yml": workflow_yaml},
        )
    else:
        gh_client.push_file(
            ".github/workflows/trigger-ado-pipeline.yml", workflow_yaml, branch=branch,
            commit_message="Add workflow to trigger Azure DevOps pipeline [CI/CD Orchestrator Agent]",
        )
        pushed_files = 1

    secret_status = "skipped"
    if set_secret:
        try:
            gh_client.set_actions_secret("ADO_PAT", ado_pat)
            secret_status = "set"
        except RuntimeError as exc:
            secret_status = f"failed: {exc}"

    return {
        "github_repo_url":   gh_client.repo_web_url(),
        "github_actions_url": gh_client.actions_web_url(),
        "ado_pipeline_id":   pipeline_id,
        "ado_pipeline_url":  ado_client.pipeline_web_url(pipeline_id),
        "files_pushed":      pushed_files,
        "files_skipped":     skipped,
        "ado_pat_secret":    secret_status,
        "note": (
            "If ado_pat_secret is not 'set', add a repo secret named ADO_PAT "
            "manually in GitHub → Settings → Secrets and variables → Actions, "
            "using your Azure DevOps PAT (Build: Read & execute scope)."
        ),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point used both by `python mcp_server.py` and the installed
    `cicd-orchestrator-mcp` console script (e.g. via `uvx --from git+...`)."""
    if "--http" in sys.argv:
        mcp.run(transport="sse")   # HTTP SSE mode on port 5001
    else:
        mcp.run(transport="stdio") # Default: stdio for Claude Desktop / mcp.json


if __name__ == "__main__":
    main()
