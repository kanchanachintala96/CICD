"""
GitHub REST API client for the CI/CD Orchestrator Agent.

Uses only Python stdlib (urllib) for repo/file operations. Encrypting and
setting Actions secrets requires PyNaCl (GitHub's secrets API mandates
libsodium sealed-box encryption) — that single call degrades gracefully with
a clear error if PyNaCl isn't installed.

Capabilities
────────────
  • Validate repo access
  • Push a single file to a repo branch (create or update)
  • Push an entire local directory (all source files) to a repo branch
  • Create/update a GitHub Actions repository secret (e.g. ADO_PAT)
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import urllib.error
import urllib.request
from typing import Any

# Reuse the same ignore lists as the Azure DevOps client for consistency
from .azure_devops import _IGNORE_DIRS, _IGNORE_FILES, _MAX_FILE_BYTES


class GitHubClient:
    """Thin GitHub REST API client (stdlib-only for repo/file operations)."""

    API_BASE = "https://api.github.com"

    def __init__(self, token: str, owner: str, repo: str) -> None:
        self.token = token
        self.owner = owner
        self.repo = repo

    # ── internal helpers ───────────────────────────────────────────────────

    def _req(self, method: str, url: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw.decode()) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"GitHub API error {exc.code}: {detail[:500]}") from exc

    def _repo_url(self, path: str = "") -> str:
        base = f"{self.API_BASE}/repos/{self.owner}/{self.repo}"
        return f"{base}/{path}" if path else base

    # ── repo validation ─────────────────────────────────────────────────────

    def validate_connection(self) -> dict:
        """Return repo info — raises if the token or repo is invalid."""
        return self._req("GET", self._repo_url())

    # ── file push ────────────────────────────────────────────────────────────

    def push_file(
        self,
        file_path: str,
        content: str,
        branch: str = "main",
        commit_message: str = "Add file [CI/CD Orchestrator Agent]",
    ) -> dict:
        """
        Create or update a single file via the Contents API.

        Parameters
        ----------
        file_path:      Path in the repo, e.g. '.github/workflows/trigger-ado.yml'
        content:        Plain-text file content
        branch:         Target branch name (default: 'main')
        commit_message: Git commit message
        """
        path = file_path.lstrip("/")
        url = self._repo_url(f"contents/{path}")

        sha = None
        try:
            existing = self._req("GET", f"{url}?ref={branch}")
            sha = existing.get("sha")
        except RuntimeError:
            pass  # file doesn't exist yet → create

        body = {
            "message": commit_message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        return self._req("PUT", url, body)

    def push_directory(
        self,
        local_path: str,
        branch: str = "main",
        commit_message: str = "Push project files [CI/CD Orchestrator Agent]",
        extra_files: dict[str, str] | None = None,
    ) -> tuple[int, list[str]]:
        """
        Push all source files from *local_path* to the GitHub repo in a single
        atomic commit, using the Git Data API (blobs + tree + commit + ref
        update) — NOT one commit per file. Pushing file-by-file via the
        Contents API triggers a separate GitHub Actions run per file, which is
        wasteful and noisy; a single commit triggers at most one run.

        Returns (file_count, skipped_files).
        """
        local_root = os.path.abspath(local_path)
        files: dict[str, str] = {}  # {repo_path: content}
        skipped: list[str] = []

        for dirpath, dirnames, filenames in os.walk(local_root):
            dirnames[:] = [
                d for d in dirnames
                if not any(fnmatch.fnmatch(d, pat) for pat in _IGNORE_DIRS)
            ]

            for filename in filenames:
                if any(fnmatch.fnmatch(filename, pat) for pat in _IGNORE_FILES):
                    skipped.append(filename)
                    continue

                abs_path = os.path.join(dirpath, filename)
                try:
                    if os.path.getsize(abs_path) > _MAX_FILE_BYTES:
                        skipped.append(filename)
                        continue
                except OSError:
                    continue

                rel = os.path.relpath(abs_path, local_root).replace("\\", "/")
                try:
                    with open(abs_path, encoding="utf-8") as fh:
                        content = fh.read()
                except (UnicodeDecodeError, OSError):
                    skipped.append(filename)  # binary files: skip (base64 push omitted for brevity)
                    continue

                files[rel] = content

        if extra_files:
            files.update(extra_files)

        if not files:
            return 0, skipped

        # Resolve current HEAD of the target branch (may not exist yet)
        try:
            ref = self._req("GET", self._repo_url(f"git/ref/heads/{branch}"))
            base_commit_sha = ref["object"]["sha"]
            base_commit = self._req("GET", self._repo_url(f"git/commits/{base_commit_sha}"))
            base_tree_sha = base_commit["tree"]["sha"]
            parents = [base_commit_sha]
        except RuntimeError:
            base_tree_sha = None
            parents = []

        # Create a blob per file, then one tree referencing all of them
        tree_entries = []
        for repo_path, content in files.items():
            blob = self._req(
                "POST",
                self._repo_url("git/blobs"),
                {"content": base64.b64encode(content.encode()).decode(), "encoding": "base64"},
            )
            tree_entries.append({
                "path": repo_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })

        tree_body: dict[str, Any] = {"tree": tree_entries}
        if base_tree_sha:
            tree_body["base_tree"] = base_tree_sha
        tree = self._req("POST", self._repo_url("git/trees"), tree_body)

        commit = self._req(
            "POST",
            self._repo_url("git/commits"),
            {"message": commit_message, "tree": tree["sha"], "parents": parents},
        )

        if parents:
            self._req(
                "PATCH",
                self._repo_url(f"git/refs/heads/{branch}"),
                {"sha": commit["sha"], "force": False},
            )
        else:
            self._req(
                "POST",
                self._repo_url("git/refs"),
                {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
            )

        return len(files), skipped

    # ── Actions administration ───────────────────────────────────────────────

    def set_actions_enabled(self, enabled: bool) -> None:
        """Enable or disable GitHub Actions entirely for this repository."""
        self._req("PUT", self._repo_url("actions/permissions"), {"enabled": enabled})

    def delete_file(
        self,
        file_path: str,
        branch: str = "main",
        commit_message: str = "Delete file [CI/CD Orchestrator Agent]",
    ) -> dict:
        """Delete a single file from the repo (no-op if it doesn't exist)."""
        path = file_path.lstrip("/")
        url = self._repo_url(f"contents/{path}")
        try:
            info = self._req("GET", f"{url}?ref={branch}")
        except RuntimeError:
            return {"deleted": False, "reason": "file not found"}
        return self._req(
            "DELETE", url,
            {"message": commit_message, "sha": info["sha"], "branch": branch},
        )

    # ── Actions secrets ──────────────────────────────────────────────────────

    def set_actions_secret(self, secret_name: str, secret_value: str) -> None:
        """
        Create/update a repository secret for GitHub Actions (e.g. ADO_PAT).

        Requires PyNaCl (`pip install pynacl`) because GitHub mandates
        libsodium sealed-box encryption for secret values.
        """
        try:
            from nacl import encoding, public
        except ImportError as exc:
            raise RuntimeError(
                "Setting GitHub Actions secrets requires PyNaCl. "
                "Install it with: pip install pynacl — or add the secret "
                "manually via GitHub → Settings → Secrets and variables → Actions."
            ) from exc

        key_info = self._req("GET", self._repo_url("actions/secrets/public-key"))
        public_key = public.PublicKey(key_info["key"], encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode())
        encrypted_b64 = base64.b64encode(encrypted).decode()

        self._req(
            "PUT",
            self._repo_url(f"actions/secrets/{secret_name}"),
            {"encrypted_value": encrypted_b64, "key_id": key_info["key_id"]},
        )

    # ── web URLs ─────────────────────────────────────────────────────────────

    def repo_web_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    def actions_web_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/actions"
