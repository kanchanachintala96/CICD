"""
AI-powered code review using EPAM AI DIAL.

Reads project source files via RAG and asks an LLM deployed on DIAL to
review the code for security issues, bugs, best practices, and
maintainability.

DIAL is an OpenAI-compatible gateway — it accepts an `api-key` header
and standard chat-completions JSON.  Any model deployed on your DIAL
instance (gpt-4o, claude-3-5-sonnet, etc.) can be used.

Environment variables (all optional — can be supplied per-request too)
-----------------------------------------------------------------------
DIAL_API_KEY   : your DIAL API key
DIAL_BASE_URL  : base URL of your DIAL deployment
                 e.g. https://ai-proxy.lab.epam.com
DIAL_MODEL     : default model name, e.g. gpt-4o
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional

from .rag import build_rag_context, estimate_tokens


# ── prompts ───────────────────────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """\
You are an expert code reviewer with deep knowledge of security, performance,
and software engineering best practices.

Analyse the provided codebase context and respond ONLY with a valid JSON object
that has exactly this structure (no markdown, no extra text):

{
  "overall_rating": "<excellent|good|fair|needs_improvement>",
  "summary": "<2-3 sentence overview of the code quality and main concerns>",
  "positive_aspects": ["<positive finding>"],
  "findings": [
    {
      "severity": "<critical|high|medium|low|info>",
      "category": "<security|bug|performance|maintainability|best_practice|style>",
      "message": "<clear, specific description of the issue>",
      "suggestion": "<actionable recommendation to fix or improve>"
    }
  ]
}

Rules:
- Focus on the most important 5-10 findings (skip trivial style issues unless critical).
- Severity guide: critical=exploitable vulnerability or data loss, high=serious bug/security risk,
  medium=notable issue affecting reliability or quality, low=minor improvement opportunity, info=observation.
- Always include at least one positive_aspect if the code has any good qualities.
- Do NOT comment on auto-generated files, lock files, or binary assets.
"""

REVIEW_USER_TEMPLATE = """\
Review the following project code and provide actionable feedback.

{context}
"""

# Default DIAL base URL
_DEFAULT_DIAL_URL = "https://ai-proxy.lab.epam.com"
_DEFAULT_DIAL_MODEL = "gpt-4o"


# ── DIAL call ─────────────────────────────────────────────────────────────────

def _call_dial(
    context: str,
    model: str,
    api_key: str,
    base_url: str,
    max_output_tokens: int,
) -> Dict[str, Any]:
    """
    Send a code-review request to an EPAM AI DIAL deployment.

    DIAL is OpenAI-compatible.  It accepts the `api-key` header (Azure style)
    and a standard chat-completions body.  Two URL patterns are tried:
      1. /openai/deployments/{model}/chat/completions  (canonical DIAL path)
      2. /chat/completions                              (plain OpenAI fallback)
    """
    import urllib.error
    import urllib.request

    messages = [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": REVIEW_USER_TEMPLATE.format(context=context)},
    ]
    payload = json.dumps({
        "messages": messages,
        "max_tokens": max_output_tokens,
        "temperature": 0.2,
    }).encode()

    base = base_url.rstrip("/")
    urls_to_try = [
        f"{base}/openai/deployments/{model}/chat/completions",
        f"{base}/chat/completions",
    ]

    last_error: Exception = RuntimeError("DIAL: no endpoint responded successfully")
    data: Dict[str, Any] = {}
    for url in urls_to_try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="ignore")
            last_error = RuntimeError(
                f"DIAL returned HTTP {exc.code} at {url}: {body}"
            )
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Cannot reach DIAL at {url}: {exc}")
    else:
        raise last_error

    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    input_tok = usage.get("prompt_tokens") or estimate_tokens(json.dumps(messages))
    output_tok = usage.get("completion_tokens") or estimate_tokens(content)
    return {
        "content": content,
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "total_tokens": input_tok + output_tok,
        "model": data.get("model", model),
    }


# ── response parser ───────────────────────────────────────────────────────────

def _parse_review_json(raw: str) -> Dict[str, Any]:
    """Extract the review JSON from LLM output, tolerating markdown fences."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {
        "overall_rating": "unknown",
        "summary": "Code review response could not be parsed.",
        "positive_aspects": [],
        "findings": [],
        "_raw": raw[:500],
    }


# ── public API ────────────────────────────────────────────────────────────────

def review_with_llm(
    project_path: str,
    dial_api_key: Optional[str] = None,
    dial_base_url: Optional[str] = None,
    model: Optional[str] = None,
    token_budget: int = 4000,
    max_output_tokens: int = 1200,
) -> Dict[str, Any]:
    """
    Review the code at *project_path* via EPAM AI DIAL and return findings.

    Parameters
    ----------
    project_path    : local path to the repository
    dial_api_key    : DIAL API key (falls back to DIAL_API_KEY env var)
    dial_base_url   : DIAL base URL (falls back to DIAL_BASE_URL env var)
    model           : model deployment name on DIAL (falls back to DIAL_MODEL env var)
    token_budget    : max tokens to send as code context
    max_output_tokens: max tokens in the LLM reply

    Returns
    -------
    Dict with keys: review, rag, token_usage, elapsed_s
    """
    t0 = time.time()

    resolved_key = dial_api_key or os.environ.get("DIAL_API_KEY", "")
    if not resolved_key:
        raise RuntimeError(
            "No DIAL API key provided. "
            "Set the DIAL_API_KEY environment variable or enter it in the form."
        )

    resolved_url = dial_base_url or os.environ.get("DIAL_BASE_URL", _DEFAULT_DIAL_URL)
    resolved_model = model or os.environ.get("DIAL_MODEL", _DEFAULT_DIAL_MODEL)

    rag = build_rag_context(project_path, token_budget=token_budget)

    llm = _call_dial(
        context=rag["context"],
        model=resolved_model,
        api_key=resolved_key,
        base_url=resolved_url,
        max_output_tokens=max_output_tokens,
    )

    review = _parse_review_json(llm["content"])
    elapsed = round(time.time() - t0, 2)

    return {
        "review": review,
        "rag": {
            "files_read": rag["files_read"],
            "context_tokens": rag["estimated_tokens"],
            "char_count": rag["char_count"],
            "token_budget": token_budget,
        },
        "token_usage": {
            "input_tokens": llm["input_tokens"],
            "output_tokens": llm["output_tokens"],
            "total_tokens": llm["total_tokens"],
            "provider": "dial",
            "model": llm["model"],
        },
        "elapsed_s": elapsed,
    }
