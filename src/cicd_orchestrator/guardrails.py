"""
Guardrails enforcement for the CI/CD Orchestrator Agent.

Loads guardrails.json from the project root and validates commands,
pipelines, and generated content against the defined policies at runtime.

This module is READ-ONLY with respect to guardrails.json — the policy
file is loaded once at startup and never modified by the agent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .pipeline import Pipeline

# Policy file lives at the project root (3 levels up from this module)
_POLICY_FILE = Path(__file__).resolve().parent.parent.parent / "guardrails.json"


class GuardrailViolation(Exception):
    """Raised when a command or pipeline violates a guardrail policy."""


class Guardrails:
    """Loads guardrails.json and enforces the defined policies."""

    def __init__(self, policy_path: Path = _POLICY_FILE):
        self._policy: dict = {}
        if policy_path.exists():
            with open(policy_path, encoding="utf-8") as f:
                self._policy = json.load(f)

    # ── section accessors ────────────────────────────────────────────────────

    @property
    def pipeline_policies(self) -> dict:
        return self._policy.get("pipeline_policies", {})

    @property
    def secrets_policy(self) -> dict:
        return self._policy.get("secrets_policy", {})

    @property
    def code_quality(self) -> dict:
        return self._policy.get("code_quality", {})

    @property
    def resource_limits(self) -> dict:
        return self._policy.get("resource_limits", {})

    # ── validators ───────────────────────────────────────────────────────────

    def check_command(self, command: str) -> Optional[str]:
        """Return a violation message if the command matches a disallowed pattern, else None."""
        for pattern in self.pipeline_policies.get("disallowed_commands", []):
            try:
                if re.search(pattern, command, re.IGNORECASE):
                    return (
                        f"Guardrail blocked: command matches disallowed pattern '{pattern}'\n"
                        f"  Command: {command}"
                    )
            except re.error:
                pass  # Skip malformed patterns rather than crashing
        return None

    def check_secrets(self, text: str) -> List[str]:
        """Return list of secret-pattern violations found in the given text."""
        if not self.secrets_policy.get("block_hardcoded_secrets", False):
            return []
        violations: List[str] = []
        for pattern in self.secrets_policy.get("secret_patterns", []):
            try:
                if re.search(pattern, text):
                    violations.append(
                        f"Possible hardcoded secret detected (pattern: {pattern})"
                    )
            except re.error:
                pass
        return violations

    def check_pipeline(self, pipeline: "Pipeline") -> List[str]:
        """Return list of policy violations for the full pipeline object."""
        violations: List[str] = []
        policies = self.pipeline_policies

        # Check max steps per stage
        max_steps = policies.get("max_steps_per_stage", 20)
        for stage_name in ("build", "test", "deploy"):
            stage_steps = [s for s in pipeline.steps if s.stage.value == stage_name]
            if len(stage_steps) > max_steps:
                violations.append(
                    f"Stage '{stage_name}' has {len(stage_steps)} steps — "
                    f"exceeds max_steps_per_stage ({max_steps})"
                )

        # Check each command against disallowed patterns
        for step in pipeline.steps:
            msg = self.check_command(step.command)
            if msg:
                violations.append(msg)

        return violations


# ── module-level singleton ────────────────────────────────────────────────────

_instance: Optional[Guardrails] = None


def get_guardrails() -> Guardrails:
    """Return the shared Guardrails instance (loaded once from guardrails.json)."""
    global _instance
    if _instance is None:
        _instance = Guardrails()
    return _instance
