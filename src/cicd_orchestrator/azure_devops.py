"""
Azure DevOps REST API client for the CI/CD Orchestrator Agent.

Uses only Python stdlib (urllib) — no extra dependencies required.

Capabilities
────────────
  • Validate credentials / project access
  • List git repositories in a project
  • Push a single file to a repo branch
  • Push an entire local directory (all source files) to a repo branch
  • Create a pipeline definition
  • Trigger a pipeline run
  • Get run status and build a web URL for the run
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import urllib.error
import urllib.request
from typing import Any

# Directories to skip when pushing a local project
_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    ".pytest_cache", "dist", "build", "target", ".eggs",
    ".tox", ".mypy_cache", ".ruff_cache", ".gradle", ".idea",
    ".mvn",  # maven wrapper binaries handled separately
})

# File name / glob patterns to skip
_IGNORE_FILES: frozenset[str] = frozenset({
    ".env", "*.pyc", "*.pyo", "*.class", "*.log", ".DS_Store",
    "Thumbs.db", "*.tmp", "*.swp", "*.jar",
})

# Max size per file to push via REST (500 KB) — larger files are skipped
_MAX_FILE_BYTES = 500 * 1024


class AzureDevOpsClient:
    """Thin Azure DevOps REST API client (no third-party deps)."""

    API_VERSION = "7.1"

    def __init__(self, org_url: str, project: str, pat: str) -> None:
        # Normalize: strip trailing slash, e.g. https://dev.azure.com/myorg
        self.org_url = org_url.rstrip("/")
        self.project = project
        # PAT auth: Base64(":<pat>")
        self._auth = base64.b64encode(f":{pat}".encode()).decode()

    # ── internal helpers ───────────────────────────────────────────────────────

    def _req(self, method: str, url: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Basic {self._auth}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"Azure DevOps API error {exc.code}: {detail[:500]}"
            ) from exc

    def _url(self, path: str, *, version: str | None = None) -> str:
        v = version or self.API_VERSION
        base = f"{self.org_url}/{self.project}/_apis/{path}"
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}api-version={v}"

    # ── connection validation ──────────────────────────────────────────────────

    def validate_connection(self) -> dict:
        """Return project info — raises if credentials or project are invalid."""
        url = (
            f"{self.org_url}/_apis/projects/{self.project}"
            f"?api-version={self.API_VERSION}"
        )
        return self._req("GET", url)

    # ── repositories ───────────────────────────────────────────────────────────

    def list_repositories(self) -> list[dict]:
        """Return all git repos in the project."""
        result = self._req("GET", self._url("git/repositories"))
        return result.get("value", [])

    def get_repository(self, repo_name_or_id: str) -> dict:
        """Find a repo by name or ID; raise ValueError if not found."""
        for repo in self.list_repositories():
            if repo["id"] == repo_name_or_id or repo["name"] == repo_name_or_id:
                return repo
        raise ValueError(
            f"Repository '{repo_name_or_id}' not found in project '{self.project}'."
        )

    # ── file push ──────────────────────────────────────────────────────────────

    def _get_existing_files(self, repo_id: str, branch: str) -> set[str]:
        """Return set of file paths that already exist in the repo at *branch*."""
        try:
            url = (
                f"{self.org_url}/{self.project}/_apis/git/repositories/{repo_id}/items"
                f"?recursionLevel=Full&versionDescriptor.version={branch}"
                f"&api-version={self.API_VERSION}"
            )
            result = self._req("GET", url)
            return {
                item["path"]
                for item in result.get("value", [])
                if item.get("gitObjectType") == "blob"
            }
        except RuntimeError:
            return set()  # empty repo or branch doesn't exist yet

    def push_directory(
        self,
        repo_id: str,
        local_path: str,
        branch: str = "main",
        commit_message: str = "Push project files [CI/CD Orchestrator Agent]",
        extra_files: dict[str, str] | None = None,
    ) -> tuple[dict, int, list[str]]:
        """
        Push all source files from *local_path* to the ADO repo in one commit.

        Parameters
        ----------
        repo_id      : UUID or name of the repository
        local_path   : Local directory whose contents to push
        branch       : Target branch (created if it doesn't exist)
        commit_message: Git commit message
        extra_files  : Additional {"/repo/path": "text content"} to include
                       (e.g. {"/azure-pipelines.yml": yaml_str})

        Returns
        -------
        (push_response, file_count, skipped_files)
        """
        local_root = os.path.abspath(local_path)
        all_files: dict[str, tuple[str, str]] = {}  # {repo_path: (content, type)}
        skipped: list[str] = []

        for dirpath, dirnames, filenames in os.walk(local_root):
            # Prune ignored directories in-place so os.walk won't recurse into them
            dirnames[:] = [
                d for d in dirnames
                if not any(fnmatch.fnmatch(d, pat) for pat in _IGNORE_DIRS)
            ]

            for filename in filenames:
                if any(fnmatch.fnmatch(filename, pat) for pat in _IGNORE_FILES):
                    skipped.append(filename)
                    continue

                abs_path = os.path.join(dirpath, filename)

                # Skip files that are too large
                try:
                    if os.path.getsize(abs_path) > _MAX_FILE_BYTES:
                        skipped.append(filename)
                        continue
                except OSError:
                    continue

                rel = os.path.relpath(abs_path, local_root).replace("\\", "/")
                repo_path = f"/{rel}"

                # Try UTF-8 text first; fall back to base64 for binary
                try:
                    with open(abs_path, encoding="utf-8") as fh:
                        content, content_type = fh.read(), "rawtext"
                except (UnicodeDecodeError, OSError):
                    try:
                        with open(abs_path, "rb") as fh:
                            content = base64.b64encode(fh.read()).decode()
                        content_type = "base64encoded"
                    except OSError:
                        skipped.append(filename)
                        continue

                all_files[repo_path] = (content, content_type)

        # Merge extra files (always text/rawtext)
        if extra_files:
            for path, content in extra_files.items():
                all_files[path] = (content, "rawtext")

        if not all_files:
            raise ValueError("No files found to push.")

        # Resolve current HEAD of target branch
        refs_url = (
            f"{self.org_url}/{self.project}/_apis/git/repositories/{repo_id}/refs"
            f"?filter=heads/{branch}&api-version={self.API_VERSION}"
        )
        refs = self._req("GET", refs_url).get("value", [])
        old_oid = refs[0]["objectId"] if refs else "0" * 40

        # Determine add vs. edit per file
        existing = self._get_existing_files(repo_id, branch)
        changes = [
            {
                "changeType": "edit" if repo_path in existing else "add",
                "item": {"path": repo_path},
                "newContent": {"content": content, "contentType": content_type},
            }
            for repo_path, (content, content_type) in all_files.items()
        ]

        push_body = {
            "refUpdates": [{"name": f"refs/heads/{branch}", "oldObjectId": old_oid}],
            "commits": [{"comment": commit_message, "changes": changes}],
        }
        response = self._req(
            "POST",
            self._url(f"git/repositories/{repo_id}/pushes"),
            push_body,
        )
        return response, len(all_files), skipped

    def push_file(
        self,
        repo_id: str,
        file_path: str,
        content: str,
        branch: str = "main",
        commit_message: str = "Add azure-pipelines.yml [CI/CD Orchestrator Agent]",
    ) -> dict:
        """
        Create or update a single file in an Azure DevOps git repo.

        Parameters
        ----------
        repo_id:        UUID or name of the repository
        file_path:      Path in the repo, e.g. '/azure-pipelines.yml'
        content:        Plain-text file content
        branch:         Target branch name (default: 'main')
        commit_message: Git commit message

        Returns the push API response dict.
        """
        # Resolve current HEAD of the target branch
        refs_url = (
            f"{self.org_url}/{self.project}/_apis/git/repositories/{repo_id}/refs"
            f"?filter=heads/{branch}&api-version={self.API_VERSION}"
        )
        refs = self._req("GET", refs_url).get("value", [])
        old_oid = refs[0]["objectId"] if refs else "0" * 40

        # Decide add vs. edit
        change_type = "add"
        try:
            item_url = (
                f"{self.org_url}/{self.project}/_apis/git/repositories/{repo_id}/items"
                f"?path={file_path}&versionDescriptor.version={branch}"
                f"&api-version={self.API_VERSION}"
            )
            self._req("GET", item_url)
            change_type = "edit"
        except RuntimeError:
            pass  # file doesn't exist yet → add

        push_body = {
            "refUpdates": [
                {"name": f"refs/heads/{branch}", "oldObjectId": old_oid}
            ],
            "commits": [
                {
                    "comment": commit_message,
                    "changes": [
                        {
                            "changeType": change_type,
                            "item": {"path": file_path},
                            "newContent": {
                                "content": content,
                                "contentType": "rawtext",
                            },
                        }
                    ],
                }
            ],
        }
        return self._req(
            "POST",
            self._url(f"git/repositories/{repo_id}/pushes"),
            push_body,
        )

    # ── pipeline definitions ───────────────────────────────────────────────────

    def list_pipelines(self) -> list[dict]:
        """Return all pipeline definitions in the project."""
        result = self._req("GET", self._url("pipelines"))
        return result.get("value", [])

    def get_pipeline_by_name(self, name: str) -> dict | None:
        """Return a pipeline definition by name, or None if not found."""
        for p in self.list_pipelines():
            if p.get("name") == name:
                return p
        return None

    def create_pipeline(
        self,
        name: str,
        repo_id: str,
        repo_name: str,
        yaml_path: str = "/azure-pipelines.yml",
        branch: str = "main",
    ) -> dict:
        """
        Create a pipeline definition pointing to a YAML file in the repo.
        Returns the created pipeline object (contains 'id' and '_links').
        """
        body = {
            "name": name,
            "folder": "\\",
            "configuration": {
                "type": "yaml",
                "path": yaml_path,
                "repository": {
                    "id": repo_id,
                    "name": repo_name,
                    "type": "azureReposGit",
                    "defaultBranch": f"refs/heads/{branch}",
                },
            },
        }
        return self._req("POST", self._url("pipelines"), body)

    # ── pipeline runs ──────────────────────────────────────────────────────────

    def set_pipeline_variables(
        self,
        pipeline_id: int,
        variables: dict,
    ) -> dict:
        """
        Set secret pipeline variables on a pipeline build definition.

        GET full definition → strip read-only fields → merge vars → PUT back.
        variables: { "VAR_NAME": "value", ... }  — stored as isSecret=True.
        """
        # Read-only fields that must be removed before PUT (ADO rejects them)
        _READ_ONLY = {
            "_links", "authoredBy", "createdDate", "latestBuild",
            "latestCompletedBuild", "uri", "url", "badgeEnabled",
            "jobAuthorizationScope",
        }

        get_url = self._url(f"build/definitions/{pipeline_id}")
        definition = self._req("GET", get_url)

        # Strip read-only fields
        for field in _READ_ONLY:
            definition.pop(field, None)

        # Merge new variables (isSecret=True so values are stored securely)
        existing_vars = definition.get("variables", {})
        for name, value in variables.items():
            existing_vars[name] = {
                "value": value,
                "isSecret": True,
                "allowOverride": False,
            }
        definition["variables"] = existing_vars

        put_url = self._url(f"build/definitions/{pipeline_id}")
        return self._req("PUT", put_url, definition)

    def run_pipeline(self, pipeline_id: int, branch: str = "main") -> dict:
        """Trigger a pipeline run. Returns the run object (contains 'id')."""
        body = {
            "resources": {
                "repositories": {
                    "self": {"refName": f"refs/heads/{branch}"}
                }
            }
        }
        return self._req(
            "POST",
            self._url(f"pipelines/{pipeline_id}/runs"),
            body,
        )

    def get_run(self, pipeline_id: int, run_id: int) -> dict:
        """Return run status/details."""
        return self._req(
            "GET",
            self._url(f"pipelines/{pipeline_id}/runs/{run_id}"),
        )

    # ── URL helpers ────────────────────────────────────────────────────────────

    def pipeline_web_url(self, pipeline_id: int) -> str:
        return (
            f"{self.org_url}/{self.project}/_build"
            f"?definitionId={pipeline_id}"
        )

    def run_web_url(self, run_id: int) -> str:
        return (
            f"{self.org_url}/{self.project}/_build/results"
            f"?buildId={run_id}&view=results"
        )
