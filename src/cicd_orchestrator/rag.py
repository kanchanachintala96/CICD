"""
RAG (Retrieval-Augmented Generation) context builder.

Reads well-known project files and assembles a codebase-context string
that is passed to the LLM.  Respects a configurable token budget so the
LLM prompt never blows up in cost.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── token estimation ─────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Estimate token count.  Uses tiktoken when available (accurate),
    falls back to the ~4-chars-per-token heuristic.
    """
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


# ── file priority list ────────────────────────────────────────────────────────

# Files read first (highest signal for LLM)
PRIORITY_FILES: List[str] = [
    "README.md", "README.rst", "README.txt",
    "package.json",
    "requirements.txt", "requirements-dev.txt",
    "pyproject.toml", "setup.py", "setup.cfg",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "go.mod", "go.sum",
    "pom.xml",
    "build.gradle", "build.gradle.kts",
    "Gemfile",
    ".github/workflows",          # directory — handled specially
    "Makefile",
    "*.csproj",                   # glob — handled specially
]

# Source file extensions to sample (up to MAX_SOURCE_FILES per extension)
SOURCE_EXTENSIONS: List[str] = [".py", ".js", ".ts", ".java", ".go", ".cs", ".rb", ".rs"]
MAX_SOURCE_FILES_PER_EXT = 2
MAX_SOURCE_CHARS = 600           # chars per source file (just a taste)
MAX_PRIORITY_CHARS = 2500        # chars per priority file


# ── context builder ───────────────────────────────────────────────────────────

def build_rag_context(
    project_path: str,
    token_budget: int = 3000,
) -> Dict[str, Any]:
    """
    Read project files and build a context string for the LLM.

    Returns
    -------
    {
        "context":           str   — the full context string to inject,
        "files_read":        list  — filenames included,
        "estimated_tokens":  int   — approximate token count of context,
        "char_count":        int   — character count of context,
        "token_budget":      int   — budget that was respected,
    }
    """
    root = Path(project_path).resolve()
    char_budget = token_budget * 4          # rough: 1 token ≈ 4 chars
    parts: List[str] = []
    files_read: List[str] = []
    total_chars = 0

    def _add(label: str, content: str, max_chars: int = MAX_PRIORITY_CHARS) -> bool:
        nonlocal total_chars
        content = content[:max_chars]
        chunk = f"### {label}\n```\n{content.strip()}\n```\n\n"
        if total_chars + len(chunk) > char_budget:
            return False
        parts.append(chunk)
        files_read.append(label)
        total_chars += len(chunk)
        return True

    # ── priority files ───────────────────────────────────────────────────────
    for name in PRIORITY_FILES:
        if total_chars >= char_budget:
            break

        if "*" in name:
            # glob pattern
            for fpath in sorted(root.glob(name))[:2]:
                if not _add(fpath.name, fpath.read_text(encoding="utf-8", errors="ignore")):
                    break
            continue

        fpath = root / name
        if fpath.is_dir():
            # e.g. .github/workflows — list yml files
            for wf in sorted(fpath.glob("*.yml"))[:2]:
                if not _add(str(wf.relative_to(root)), wf.read_text(encoding="utf-8", errors="ignore")):
                    break
        elif fpath.is_file():
            _add(name, fpath.read_text(encoding="utf-8", errors="ignore"))

    # ── source file samples ──────────────────────────────────────────────────
    for ext in SOURCE_EXTENSIONS:
        if total_chars >= char_budget:
            break
        candidates = [
            f for f in sorted(root.glob(f"**/*{ext}"))
            if "venv" not in str(f) and "node_modules" not in str(f)
               and ".git" not in str(f) and "__pycache__" not in str(f)
        ]
        for fpath in candidates[:MAX_SOURCE_FILES_PER_EXT]:
            if total_chars >= char_budget:
                break
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            _add(str(fpath.relative_to(root)), content, MAX_SOURCE_CHARS)

    context = "\n".join(parts)
    return {
        "context": context,
        "files_read": files_read,
        "estimated_tokens": estimate_tokens(context),
        "char_count": len(context),
        "token_budget": token_budget,
    }
