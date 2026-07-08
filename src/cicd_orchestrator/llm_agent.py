"""
LLM Agent for intelligent CI/CD pipeline generation.

Supported providers
-------------------
• dial    — EPAM AI DIAL gateway (OpenAI-compatible, any model behind DIAL)
• openai  — OpenAI API (gpt-4o-mini, gpt-4o, …)

Flow
----
  1. RAG builds a codebase-context string from project files.
  2. Context + system prompt → LLM.
  3. LLM returns JSON describing the optimal pipeline.
  4. We parse it, attach token usage, return the full result to the caller.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from .rag import build_rag_context, estimate_tokens


# ── prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert CI/CD engineer agent.
Analyse the provided codebase context and respond ONLY with a valid JSON object
that has exactly this structure (no markdown, no extra text):

{
  "project_type": "<python|nodejs|java-maven|java-gradle|go|dotnet|ruby|docker|unknown>",
  "reasoning": "<short explanation of detected tech stack>",
  "recommended_steps": [
    { "stage": "<build|test|deploy>", "name": "<step name>", "command": "<shell command>" }
  ],
  "deploy_type": "<docker|kubernetes|script|null>",
  "docker_image": "<image name or null>",
  "suggestions": ["<optional recommendation>"]
}
"""

USER_TEMPLATE = """\
Analyse the following project and generate an optimal CI/CD pipeline.

{context}
"""


# ── OpenAI provider ───────────────────────────────────────────────────────────

def _call_openai(
    context: str,
    model: str,
    api_key: str,
    max_output_tokens: int,
) -> Dict[str, Any]:
    try:
        import openai  # type: ignore
    except ImportError:
        raise RuntimeError(
            "openai package not installed. "
            "Run: pip install openai   (or add it to pyproject.toml)"
        )

    client = openai.OpenAI(api_key=api_key)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(context=context)},
    ]
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_output_tokens,
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    usage = resp.usage
    return {
        "content": resp.choices[0].message.content,
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "model": model,
        "provider": "openai",
    }



# ── DIAL provider ─────────────────────────────────────────────────────────────

def _call_dial(
    context: str,
    model: str,
    api_key: str,
    base_url: str,
    max_output_tokens: int,
) -> Dict[str, Any]:
    """Call an EPAM AI DIAL deployment (OpenAI-compatible gateway)."""
    import urllib.error
    import urllib.request

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(context=context)},
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
            headers={"api-key": api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(
                f"DIAL HTTP {exc.code} at {url}: {exc.read().decode(errors='ignore')}"
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
        "provider": "dial",
    }


# ── response parser ───────────────────────────────────────────────────────────

def _parse_llm_json(raw: str) -> Dict[str, Any]:
    """Extract JSON from LLM output, even if wrapped in markdown fences."""
    raw = raw.strip()
    # strip ```json … ``` fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # last resort: grab first {...} block
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {
        "project_type": "unknown",
        "reasoning": "LLM response could not be parsed as JSON.",
        "recommended_steps": [],
        "deploy_type": None,
        "docker_image": None,
        "suggestions": [],
        "_raw": raw[:500],
    }


# ── cost table ────────────────────────────────────────────────────────────────

# USD per 1 million tokens: (input_price, output_price)
_OPENAI_PRICES: Dict[str, tuple] = {
    "gpt-4o-mini":      (0.15,  0.60),
    "gpt-4o":           (2.50, 10.00),
    "gpt-4-turbo":      (10.0, 30.00),
    "gpt-3.5-turbo":    (0.50,  1.50),
    "o1-mini":          (3.00, 12.00),
    "o1":               (15.0, 60.00),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    prices = _OPENAI_PRICES.get(model)
    if not prices:
        return None
    return round(
        (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000,
        6,
    )


# ── pipeline review prompt ────────────────────────────────────────────────────

PIPELINE_REVIEW_SYSTEM = """\
You are a senior DevOps engineer reviewing a CI/CD pipeline configuration.
Given the project context and the current pipeline steps, suggest concrete improvements.
Respond ONLY with a valid JSON object (no markdown, no extra text):

{
  "summary": "<one sentence overall assessment>",
  "score": <integer 1-10>,
  "suggestions": [
    {
      "title": "<short title>",
      "priority": "<high|medium|low>",
      "category": "<security|performance|reliability|best-practice|coverage>",
      "description": "<what to add or change and why>",
      "command": "<the shell command or YAML step to add, if applicable, else null>"
    }
  ]
}
"""

PIPELINE_REVIEW_USER = """\
Project context:
{context}

Current pipeline steps configured by the agent:
{pipeline_steps}

Review this pipeline and suggest improvements.
"""


def review_pipeline_with_llm(
    project_path: str,
    pipeline_steps: list,
    dial_api_key: Optional[str] = None,
    dial_base_url: Optional[str] = None,
    model: Optional[str] = None,
    token_budget: int = 3000,
    max_output_tokens: int = 1000,
) -> Dict[str, Any]:
    """
    Review the configured pipeline steps with DIAL and return improvement suggestions.

    Parameters
    ----------
    project_path   : local path to the repository (for RAG context)
    pipeline_steps : list of step dicts from the pipeline preview
    dial_api_key   : DIAL API key (falls back to DIAL_API_KEY env var)
    dial_base_url  : DIAL base URL (falls back to DIAL_BASE_URL env var)
    model          : model name (falls back to DIAL_MODEL env var)
    """
    t0 = time.time()

    rag = build_rag_context(project_path, token_budget=token_budget)

    steps_text = "\n".join(
        f"  [{s.get('stage','build').upper()}] {s.get('name','')} — {s.get('command','')}"
        for s in pipeline_steps
    )

    resolved_model = model or os.environ.get("DIAL_MODEL", "gpt-4o")
    resolved_key   = dial_api_key or os.environ.get("DIAL_API_KEY", "")
    resolved_url   = dial_base_url or os.environ.get("DIAL_BASE_URL", "https://ai-proxy.lab.epam.com")

    if not resolved_key:
        raise RuntimeError("No DIAL API key found. Set DIAL_API_KEY in .env or enter it.")

    import urllib.request, urllib.error
    messages = [
        {"role": "system", "content": PIPELINE_REVIEW_SYSTEM},
        {"role": "user",   "content": PIPELINE_REVIEW_USER.format(
            context=rag["context"], pipeline_steps=steps_text
        )},
    ]
    payload = json.dumps({
        "messages": messages,
        "max_tokens": max_output_tokens,
        "temperature": 0.3,
    }).encode()

    base = resolved_url.rstrip("/")
    urls_to_try = [
        f"{base}/openai/deployments/{resolved_model}/chat/completions",
        f"{base}/chat/completions",
    ]
    last_error: Exception = RuntimeError("DIAL: no endpoint responded")
    data: Dict[str, Any] = {}
    for url in urls_to_try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"api-key": resolved_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(f"DIAL HTTP {exc.code}: {exc.read().decode(errors='ignore')}")
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Cannot reach DIAL: {exc}")
    else:
        raise last_error

    content = data["choices"][0]["message"]["content"]
    usage   = data.get("usage", {})
    result  = _parse_llm_json(content)
    elapsed = round(time.time() - t0, 2)

    return {
        "review":  result,
        "elapsed_s": elapsed,
        "model":   data.get("model", resolved_model),
        "token_usage": {
            "input_tokens":  usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens":  usage.get("total_tokens", 0),
        },
    }


# ── public API ────────────────────────────────────────────────────────────────

def analyze_with_llm(
    project_path: str,
    provider: str = "dial",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    dial_base_url: Optional[str] = None,
    token_budget: int = 3000,
    max_output_tokens: int = 800,
) -> Dict[str, Any]:
    """
    Analyse *project_path* with an LLM and return a pipeline suggestion.

    Parameters
    ----------
    project_path   : local path to the repository
    provider       : "dial" or "openai"
    model          : override the default model for the chosen provider
    api_key        : API key (DIAL or OpenAI; falls back to env vars)
    dial_base_url  : DIAL base URL (falls back to DIAL_BASE_URL env var)
    token_budget   : max tokens to send as context
    max_output_tokens : max tokens in the LLM reply
    """
    t0 = time.time()

    rag = build_rag_context(project_path, token_budget=token_budget)

    provider = provider.lower().strip()
    if provider == "dial":
        resolved_model = model or os.environ.get("DIAL_MODEL", "gpt-4o")
        resolved_key = api_key or os.environ.get("DIAL_API_KEY", "")
        if not resolved_key:
            raise RuntimeError(
                "No DIAL API key found. "
                "Set the DIAL_API_KEY environment variable or enter it in the form."
            )
        resolved_url = dial_base_url or os.environ.get("DIAL_BASE_URL", "https://ai-proxy.lab.epam.com")
        llm = _call_dial(rag["context"], resolved_model, resolved_key, resolved_url, max_output_tokens)

    elif provider == "openai":
        resolved_model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_key:
            raise RuntimeError(
                "No OpenAI API key found. "
                "Set the OPENAI_API_KEY environment variable or pass api_key=."
            )
        llm = _call_openai(rag["context"], resolved_model, resolved_key, max_output_tokens)

    else:
        raise ValueError(
            f"Unknown provider '{provider}'. Choose 'dial' or 'openai'."
        )

    suggestion = _parse_llm_json(llm["content"])

    cost = None
    if provider == "openai":
        cost = _estimate_cost(llm["model"], llm["input_tokens"], llm["output_tokens"])

    elapsed = round(time.time() - t0, 2)

    return {
        "suggestion": suggestion,
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
            "token_budget": token_budget,
            "max_output_tokens": max_output_tokens,
            "budget_used_pct": round(
                min(100, rag["estimated_tokens"] / max(1, token_budget) * 100), 1
            ),
            "estimated_cost_usd": cost,
            "provider": provider,
            "model": llm["model"],
        },
        "elapsed_s": elapsed,
    }
